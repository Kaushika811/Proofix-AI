"""AST-driven extraction of the code a repair actually needs.

Given a file and a target function, this walks the module's AST spans and pulls
out the target plus everything semantically reachable from it — callees, callers,
the enclosing class, referenced classes and constants, and only the imports those
symbols use. Everything else in the file is left out.

Selection is graph-driven, never textual: a symbol is included because the AST
says the target reaches it. Naming conventions are used *only* to label an
already-selected symbol (a selected `validate_*` function is tagged
`validation_helper`), never to decide inclusion. That keeps the layer
repository-agnostic.

No regular expressions are used for any structural decision in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from backend.models.context import ExtractedSymbol
from backend.services.python_ast_parser import (
    ClassSpan,
    ConstantSpan,
    FunctionSpan,
    ParsedModule,
    parse_source,
)

# Prefixes used to *label* selected helpers. Not selection criteria.
_VALIDATION_PREFIXES = ("validate", "check", "verify", "ensure", "assert", "is_", "has_")
_CONFIG_HINTS = ("config", "settings", "options", "env")

# How many hops of the intra-module call graph to follow out from the target.
DEFAULT_CALL_DEPTH = 2

# Helpers beyond this count are emitted as signature + docstring only.
DEFAULT_FULL_BODY_HELPERS = 6


@dataclass
class FileExtraction:
    """Everything pulled from one file."""

    file: str
    source: str = ""
    imports: list[str] = field(default_factory=list)
    functions: list[ExtractedSymbol] = field(default_factory=list)
    classes: list[ExtractedSymbol] = field(default_factory=list)
    constants: list[ExtractedSymbol] = field(default_factory=list)
    target: ExtractedSymbol | None = None
    parsed: ParsedModule | None = None
    degraded: bool = False
    degraded_reason: str = ""

    @property
    def symbols(self) -> list[ExtractedSymbol]:
        return [*self.classes, *self.functions, *self.constants]

    @property
    def total_lines(self) -> int:
        return sum(s.line_count for s in self.symbols)


def _slice(source_lines: list[str], start: int, end: int) -> str:
    """Verbatim source for an inclusive 1-based line range."""
    if start < 1 or end < start:
        return ""
    chunk = source_lines[start - 1 : end]
    return "".join(chunk).rstrip() + "\n"


def _signature_only(source_lines: list[str], span: FunctionSpan) -> str:
    """`def` line(s) plus docstring — enough to call it, not enough to bloat."""
    body_start = span.lineno
    header = _slice(source_lines, span.span_start, body_start)
    if not span.docstring:
        return header.rstrip() + "\n    ...\n"
    doc = span.docstring.strip().splitlines()[0]
    return header.rstrip() + f'\n    """{doc}"""\n    ...\n'


def _class_shell(
    source_lines: list[str],
    span: ClassSpan,
    parsed: ParsedModule,
    *,
    keep_methods: set[str],
) -> str:
    """Class header, docstring and class-level attributes, without method bodies.

    A method's enclosing class is context — the repair needs its shape, bases and
    fields. Splicing in the whole body would re-emit the target method and every
    unrelated sibling, which is the opposite of the point. Methods explicitly
    kept elsewhere are listed as a comment so the model knows they exist.
    """
    method_starts = [
        f.span_start
        for f in parsed.function_spans
        if f.parent_class == span.name and f.span_start > span.lineno
    ]
    if not method_starts:
        return _slice(source_lines, span.span_start, span.end_lineno)

    shell = _slice(source_lines, span.span_start, min(method_starts) - 1).rstrip()
    others = [m for m in span.methods if m not in keep_methods]
    if others:
        shell += "\n    # other methods omitted: " + ", ".join(sorted(others))
    return shell + "\n"


def _label_function(span: FunctionSpan, default: str) -> str:
    lowered = span.name.lower().lstrip("_")
    if lowered.startswith(_VALIDATION_PREFIXES):
        return "validation_helper"
    return default


def _label_class(span: ClassSpan) -> str:
    if span.is_dataclass:
        return "dataclass"
    if span.is_typed_model:
        return "typed_model"
    lowered = span.name.lower()
    if any(hint in lowered for hint in _CONFIG_HINTS):
        return "config_object"
    return "class_definition"


def _label_constant(span: ConstantSpan) -> str:
    lowered = span.name.lower()
    if any(hint in lowered for hint in _CONFIG_HINTS):
        return "config_object"
    return "constant"


def find_target_span(parsed: ParsedModule, target_function: str | None) -> FunctionSpan | None:
    """Resolve a function name to a span, preferring module-level over methods.

    Accepts a bare name or a `Class.method` qualname.
    """
    if not target_function:
        return None

    for span in parsed.function_spans:
        if span.qualname == target_function:
            return span

    matches = [s for s in parsed.function_spans if s.name == target_function]
    if not matches:
        return None

    top_level = [s for s in matches if not s.is_method]
    if top_level:
        return top_level[0]
    # Ambiguous method name across classes: refuse to guess.
    return matches[0] if len(matches) == 1 else None


def _reachable_functions(
    parsed: ParsedModule,
    root: FunctionSpan,
    depth: int,
) -> dict[str, int]:
    """Intra-module callees reachable from `root`, mapped to their hop distance."""
    by_name: dict[str, FunctionSpan] = {}
    for span in parsed.function_spans:
        by_name.setdefault(span.name, span)

    distances: dict[str, int] = {}
    frontier = [(root, 0)]
    seen = {root.qualname}

    while frontier:
        span, hops = frontier.pop(0)
        if hops >= depth:
            continue
        for called in span.calls:
            callee = by_name.get(called)
            if callee is None or callee.qualname in seen:
                continue
            seen.add(callee.qualname)
            distances[callee.qualname] = hops + 1
            frontier.append((callee, hops + 1))

    return distances


def _callers_of(parsed: ParsedModule, target: FunctionSpan) -> list[FunctionSpan]:
    return [
        span
        for span in parsed.function_spans
        if span.qualname != target.qualname and target.name in span.calls
    ]


def _required_imports(parsed: ParsedModule, used_names: set[str]) -> list[str]:
    """Import statements whose bound names are actually referenced."""
    kept: list[str] = []
    for span in parsed.import_spans:
        bound = {name.split(".")[0] for name in span.names}
        if bound & used_names:
            kept.append(span.source or _render_import(span))
    return list(dict.fromkeys(kept))


def _render_import(span) -> str:
    names = ", ".join(span.names)
    if span.module:
        return f"from {span.module} import {names}"
    return f"import {names}"


def extract_from_source(
    source: str,
    file: str,
    *,
    target_function: str | None = None,
    extra_symbols: tuple[str, ...] = (),
    include_callers: bool = True,
    call_depth: int = DEFAULT_CALL_DEPTH,
    full_body_helpers: int = DEFAULT_FULL_BODY_HELPERS,
) -> FileExtraction:
    """Extract the target function and everything it semantically depends on."""
    parsed = parse_source(source)
    if parsed is None:
        # Unparseable file: the caller falls back to whole-file context rather
        # than silently shipping nothing.
        return FileExtraction(
            file=file,
            source=source,
            degraded=True,
            degraded_reason="source could not be parsed",
        )

    lines = source.splitlines(keepends=True)
    target = find_target_span(parsed, target_function)

    if target is None and not extra_symbols:
        return _outline_extraction(source, file, parsed, lines)

    functions: list[ExtractedSymbol] = []
    classes: list[ExtractedSymbol] = []
    constants: list[ExtractedSymbol] = []
    used_names: set[str] = set()
    emitted: set[str] = set()

    def emit_function(span: FunctionSpan, kind: str, reason: str, *, full_body: bool) -> None:
        if span.qualname in emitted:
            return
        emitted.add(span.qualname)
        body = (
            _slice(lines, span.span_start, span.end_lineno)
            if full_body
            else _signature_only(lines, span)
        )
        functions.append(
            ExtractedSymbol(
                name=span.name,
                qualname=span.qualname,
                file=file,
                kind=_label_function(span, kind),
                lineno=span.span_start,
                end_lineno=span.end_lineno,
                source=body,
                signature_only=not full_body,
                docstring=span.docstring,
                reason=reason,
            )
        )
        used_names.update(span.references)
        used_names.update(span.calls)
        used_names.update(span.decorators)

    # 1. The target itself, always in full.
    target_extract: ExtractedSymbol | None = None
    if target is not None:
        emit_function(target, "target_function", "resolved repair target", full_body=True)
        target_extract = functions[0]

    # 2. Explicitly requested extra symbols (e.g. names named in a citation).
    by_name = {s.name: s for s in parsed.function_spans}
    for name in extra_symbols:
        span = by_name.get(name)
        if span is not None:
            emit_function(span, "dependent_function", f"named in evidence: {name}", full_body=True)

    # 3. Callees, nearest first.
    if target is not None:
        distances = _reachable_functions(parsed, target, call_depth)
        ordered = sorted(distances.items(), key=lambda kv: (kv[1], kv[0]))
        for idx, (qualname, hops) in enumerate(ordered):
            span = next((s for s in parsed.function_spans if s.qualname == qualname), None)
            if span is None:
                continue
            emit_function(
                span,
                "called_function",
                f"called by target ({hops} hop{'s' if hops > 1 else ''})",
                full_body=idx < full_body_helpers,
            )

        # 4. Callers — signature only; they establish the contract, and their
        #    bodies are rarely what needs changing.
        if include_callers:
            for span in _callers_of(parsed, target):
                emit_function(span, "caller_function", "calls the target", full_body=False)

    # 5. Classes: the target's own class, plus any class the kept code names.
    wanted_classes: set[str] = set()
    if target is not None and target.parent_class:
        wanted_classes.add(target.parent_class)
    wanted_classes |= {c.name for c in parsed.class_spans if c.name in used_names}

    kept_methods = {q.split(".")[-1] for q in emitted}
    for span in parsed.class_spans:
        if span.name not in wanted_classes:
            continue
        encloses_target = target is not None and span.name == target.parent_class
        classes.append(
            ExtractedSymbol(
                name=span.name,
                qualname=span.name,
                file=file,
                kind=_label_class(span),
                lineno=span.span_start,
                end_lineno=span.end_lineno,
                source=_class_shell(lines, span, parsed, keep_methods=kept_methods),
                signature_only=bool(span.methods),
                docstring=span.docstring,
                reason=(
                    "encloses the target"
                    if encloses_target
                    else "referenced by kept code"
                ),
            )
        )
        used_names.update(span.bases)
        used_names.update(span.decorators)

    # 6. Module constants the kept code reads.
    for span in parsed.constant_spans:
        if span.name not in used_names:
            continue
        constants.append(
            ExtractedSymbol(
                name=span.name,
                qualname=span.name,
                file=file,
                kind=_label_constant(span),
                lineno=span.lineno,
                end_lineno=span.end_lineno,
                source=_slice(lines, span.lineno, span.end_lineno),
                reason="referenced by kept code",
            )
        )
        if span.annotation:
            used_names.add(span.annotation)

    # 7. Only the imports the kept code actually binds.
    imports = _required_imports(parsed, used_names)

    return FileExtraction(
        file=file,
        source=source,
        imports=imports,
        functions=functions,
        classes=classes,
        constants=constants,
        target=target_extract,
        parsed=parsed,
        degraded=target is None,
        degraded_reason="" if target is not None else "no target function resolved",
    )


def _outline_extraction(
    source: str,
    file: str,
    parsed: ParsedModule,
    lines: list[str],
) -> FileExtraction:
    """Structural map of a file when no single target function could be resolved.

    Every import, class shell and module constant in full, plus every top-level
    function as a signature. That orients the model over the whole module at a
    fraction of its size — and is strictly more informative than the raw
    character prefix A7 would otherwise fall back to.
    """
    classes = [
        ExtractedSymbol(
            name=span.name,
            qualname=span.name,
            file=file,
            kind=_label_class(span),
            lineno=span.span_start,
            end_lineno=span.end_lineno,
            source=_class_shell(lines, span, parsed, keep_methods=set()),
            signature_only=bool(span.methods),
            docstring=span.docstring,
            reason="module outline (no single target function resolved)",
        )
        for span in parsed.class_spans
    ]

    functions = [
        ExtractedSymbol(
            name=span.name,
            qualname=span.qualname,
            file=file,
            kind=_label_function(span, "utility"),
            lineno=span.span_start,
            end_lineno=span.end_lineno,
            source=_signature_only(lines, span),
            signature_only=True,
            docstring=span.docstring,
            reason="module outline",
        )
        for span in parsed.function_spans
        if not span.is_method
    ]

    constants = [
        ExtractedSymbol(
            name=span.name,
            qualname=span.name,
            file=file,
            kind=_label_constant(span),
            lineno=span.lineno,
            end_lineno=span.end_lineno,
            source=_slice(lines, span.lineno, span.end_lineno),
            reason="module-level constant",
        )
        for span in parsed.constant_spans
    ]

    imports = [span.source or _render_import(span) for span in parsed.import_spans]

    return FileExtraction(
        file=file,
        source=source,
        imports=list(dict.fromkeys(imports)),
        functions=functions,
        classes=classes,
        constants=constants,
        target=None,
        parsed=parsed,
        degraded=True,
        degraded_reason="no target function resolved — outline only",
    )


def extract_from_file(
    repo_path: Path,
    file: str,
    **kwargs,
) -> FileExtraction:
    """Read a repo-relative file and extract from it."""
    full = repo_path / file
    try:
        source = full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return FileExtraction(
            file=file,
            degraded=True,
            degraded_reason=f"unreadable: {exc.__class__.__name__}",
        )
    return extract_from_source(source, file, **kwargs)


def render_focused_context(extractions: list[FileExtraction]) -> str:
    """Render extracted symbols into the block A7 reasons over.

    One fenced block per file rather than per symbol: the scaffolding is pure
    overhead against the prompt budget, so symbols are separated by short comment
    markers inside a single fence instead.
    """
    blocks: list[str] = []

    for extraction in extractions:
        if not extraction.symbols and not extraction.imports:
            continue

        # Emission order matters: the block may be truncated to fit the prompt
        # budget, so everything the repair cannot proceed without comes first.
        parts: list[str] = []
        if extraction.imports:
            parts.append("\n".join(extraction.imports))

        for symbol in extraction.constants:
            parts.append(symbol.source.rstrip())

        enclosing = [c for c in extraction.classes if c.reason == "encloses the target"]
        other_classes = [c for c in extraction.classes if c.reason != "encloses the target"]

        for symbol in enclosing:
            parts.append(symbol.source.rstrip())

        target = [f for f in extraction.functions if f.kind == "target_function"]
        helpers = [f for f in extraction.functions if f.kind != "target_function"]

        for symbol in target:
            parts.append(f"# >>> REPAIR TARGET\n{symbol.source.rstrip()}")

        for symbol in other_classes:
            parts.append(symbol.source.rstrip())

        for symbol in helpers:
            marker = "  # signature only" if symbol.signature_only else ""
            parts.append(f"# {symbol.kind}{marker}\n{symbol.source.rstrip()}")

        blocks.append(f"# {extraction.file}\n```python\n" + "\n\n".join(parts) + "\n```")

    return "\n\n".join(blocks)
