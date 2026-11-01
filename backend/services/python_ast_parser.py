"""Single-pass Python AST parsing shared by import graph and role classification."""

from __future__ import annotations

import ast
import tokenize
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel, Field


class ImportSpan(BaseModel):
    """A single import statement with its exact source location."""

    names: list[str] = Field(default_factory=list)
    module: str | None = None
    lineno: int = 0
    end_lineno: int = 0
    source: str = ""


class FunctionSpan(BaseModel):
    """A function or method with the line range needed to extract it verbatim."""

    name: str
    qualname: str = ""
    # First line of the whole construct including decorators; `lineno` is the
    # `def` itself. Slicing from `span_start` keeps decorators attached.
    span_start: int = 0
    lineno: int = 0
    end_lineno: int = 0
    is_async: bool = False
    is_method: bool = False
    parent_class: str | None = None
    decorators: list[str] = Field(default_factory=list)
    # Bare-name invocations, e.g. `helper()`. Resolvable against module scope.
    calls: list[str] = Field(default_factory=list)
    # Attribute invocations, e.g. `store.get_events()`. The receiver's type is
    # unknown, so these must never resolve against module-level names — a local
    # `def get_events` does not make `store.get_events()` a self-call.
    attribute_calls: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    docstring: str | None = None
    # McCabe cyclomatic complexity. Additive and defaulted; a cache payload
    # written before this existed deserializes with 0, which the risk engine
    # treats as "unmeasured" rather than "simple" — the cache key version guards
    # against reading such a payload back in the first place.
    complexity: int = 0


class ClassSpan(BaseModel):
    """A class definition, including the shape hints A5.5 ranks on."""

    name: str
    span_start: int = 0
    lineno: int = 0
    end_lineno: int = 0
    bases: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    docstring: str | None = None
    is_dataclass: bool = False
    is_typed_model: bool = False


class ConstantSpan(BaseModel):
    """A module-level assignment — config surface and shared literals."""

    name: str
    lineno: int = 0
    end_lineno: int = 0
    annotation: str | None = None


class ParsedModule(BaseModel):
    imports: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    bases: list[str] = Field(default_factory=list)
    docstring: str | None = None
    exported_symbols: list[str] = Field(default_factory=list)
    top_level_comments: list[str] = Field(default_factory=list)

    # Span metadata for A5.5 context extraction. Additive and defaulted, so a
    # SIG cache payload written before these existed still deserializes; the
    # cache key version guards against silently extracting from an empty index.
    import_spans: list[ImportSpan] = Field(default_factory=list)
    function_spans: list[FunctionSpan] = Field(default_factory=list)
    class_spans: list[ClassSpan] = Field(default_factory=list)
    constant_spans: list[ConstantSpan] = Field(default_factory=list)


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _module_level_comments(source: str) -> list[str]:
    comments: list[str] = []
    try:
        tokens = tokenize.tokenize(BytesIO(source.encode("utf-8")).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT and tok.start[1] == 0:
                text = tok.string.lstrip("#").strip()
                if text:
                    comments.append(text)
    except tokenize.TokenError:
        pass
    return comments[:10]


DATACLASS_DECORATORS = frozenset({"dataclass", "attrs", "define", "attr"})
TYPED_MODEL_BASES = frozenset({"BaseModel", "TypedDict", "NamedTuple", "Enum", "IntEnum", "StrEnum"})


def _annotation_name(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _called_names(node: ast.AST) -> tuple[list[str], list[str]]:
    """Invoked names inside a node, split into bare calls and attribute calls."""
    bare: list[str] = []
    attribute: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                bare.append(func.id)
            elif isinstance(func, ast.Attribute):
                attribute.append(func.attr)
    return list(dict.fromkeys(bare)), list(dict.fromkeys(attribute))


def _referenced_names(node: ast.AST) -> list[str]:
    """Every bare name read inside a node — used to pull in constants."""
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.append(child.id)
        elif isinstance(child, ast.Attribute):
            base = child
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                names.append(base.id)
    return list(dict.fromkeys(names))


# Nodes that introduce a branch in control flow. McCabe complexity is the count
# of these plus one, which is the number of linearly independent paths through
# the function. Boolean operators count per extra operand: `a and b and c` is
# two additional branches, not one.
_BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
    ast.IfExp,
    ast.comprehension,
    ast.Match,
)


def cyclomatic_complexity(node: ast.AST) -> int:
    """McCabe complexity of one function body. Never below 1."""
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, _BRANCH_NODES):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += max(0, len(child.values) - 1)
        elif isinstance(child, ast.match_case) and child.guard is not None:
            complexity += 1
    return complexity


def _span_start(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    """First source line of the construct, decorators included."""
    linenos = [node.lineno] + [d.lineno for d in node.decorator_list]
    return min(linenos)


def _function_span(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parent_class: str | None = None,
) -> FunctionSpan:
    bare_calls, attribute_calls = _called_names(node)
    return FunctionSpan(
        name=node.name,
        qualname=f"{parent_class}.{node.name}" if parent_class else node.name,
        span_start=_span_start(node),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_method=parent_class is not None,
        parent_class=parent_class,
        decorators=[n for n in (_decorator_name(d) for d in node.decorator_list) if n],
        calls=bare_calls,
        attribute_calls=attribute_calls,
        references=_referenced_names(node),
        docstring=ast.get_docstring(node),
        complexity=cyclomatic_complexity(node),
    )


def collect_spans(
    tree: ast.Module,
) -> tuple[list[ImportSpan], list[FunctionSpan], list[ClassSpan], list[ConstantSpan]]:
    """Collect span metadata for every top-level construct, plus methods.

    Nested functions are intentionally not indexed separately: they are extracted
    as part of their enclosing function's span, which is what a repair needs.
    """
    imports: list[ImportSpan] = []
    functions: list[FunctionSpan] = []
    classes: list[ClassSpan] = []
    constants: list[ConstantSpan] = []

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            imports.append(
                ImportSpan(
                    names=[alias.asname or alias.name for alias in node.names],
                    module=module,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno or node.lineno,
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_function_span(node))
        elif isinstance(node, ast.ClassDef):
            decorators = [n for n in (_decorator_name(d) for d in node.decorator_list) if n]
            bases = [n for n in (_base_name(b) for b in node.bases) if n]
            methods: list[str] = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(child.name)
                    functions.append(_function_span(child, parent_class=node.name))
            classes.append(
                ClassSpan(
                    name=node.name,
                    span_start=_span_start(node),
                    lineno=node.lineno,
                    end_lineno=node.end_lineno or node.lineno,
                    bases=bases,
                    decorators=decorators,
                    methods=methods,
                    docstring=ast.get_docstring(node),
                    is_dataclass=any(d in DATACLASS_DECORATORS for d in decorators),
                    is_typed_model=any(b in TYPED_MODEL_BASES for b in bases),
                )
            )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants.append(
                        ConstantSpan(
                            name=target.id,
                            lineno=node.lineno,
                            end_lineno=node.end_lineno or node.lineno,
                        )
                    )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            constants.append(
                ConstantSpan(
                    name=node.target.id,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno or node.lineno,
                    annotation=_annotation_name(node.annotation),
                )
            )

    return imports, functions, classes, constants


def parse_source(source: str) -> ParsedModule | None:
    """Parse source text into a fully populated ParsedModule, or None if invalid."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return parsed_module_from_tree(tree, source)


def parsed_module_from_tree(tree: ast.Module, source: str) -> ParsedModule:
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []
    decorators: list[str] = []
    bases: list[str] = []
    exported: list[str] = []
    all_names: list[str] | None = None

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
            for dec in node.decorator_list:
                name = _decorator_name(dec)
                if name:
                    decorators.append(name)
            if not node.name.startswith("_"):
                exported.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
            for dec in node.decorator_list:
                name = _decorator_name(dec)
                if name:
                    decorators.append(name)
            for base in node.bases:
                b = _base_name(base)
                if b:
                    bases.append(b)
            if not node.name.startswith("_"):
                exported.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        all_names = []
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                all_names.append(elt.value)

    exported_symbols = all_names if all_names is not None else list(dict.fromkeys(exported))

    import_spans, function_spans, class_spans, constant_spans = collect_spans(tree)
    for span in import_spans:
        span.source = ast.get_source_segment(source, _node_at(tree, span.lineno)) or ""

    return ParsedModule(
        imports=list(dict.fromkeys(imports)),
        classes=classes,
        functions=functions,
        decorators=list(dict.fromkeys(decorators)),
        bases=list(dict.fromkeys(bases)),
        docstring=ast.get_docstring(tree),
        exported_symbols=exported_symbols,
        top_level_comments=_module_level_comments(source),
        import_spans=import_spans,
        function_spans=function_spans,
        class_spans=class_spans,
        constant_spans=constant_spans,
    )


def _node_at(tree: ast.Module, lineno: int) -> ast.AST:
    for node in tree.body:
        if node.lineno == lineno:
            return node
    return tree


def parse_python_file(repo_path: Path, relative_path: str) -> ParsedModule | None:
    """Parse a single Python file once. Returns None if missing or syntactically invalid."""
    full = repo_path.resolve() / relative_path
    if not full.is_file():
        return None
    try:
        source = full.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None
    return parsed_module_from_tree(tree, source)
