"""Build the structural repository graph from AST spans.

Nodes address every entity a repair might reason about — packages, directories,
files, classes, functions, methods, module variables, tests, configuration — and
edges record how they are arranged and how they refer to one another.

Everything derives from `python_ast_parser` spans, which the pipeline already
computes once per file. This module adds no parsing of its own beyond reading
the files it is given, and it is deterministic: node ids are content-addressed by
path and symbol name, never by iteration order.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from backend.models.repository_graph import (
    EdgeKind,
    NodeKind,
    RepoEdge,
    RepoNode,
    RepositoryGraph,
)
from backend.services.python_ast_parser import ParsedModule, parse_source
from backend.services.repo_layout import (
    is_excluded_path,
    resolve_scan_paths,
)

# Filenames that mark a directory as a Python package.
PACKAGE_MARKER = "__init__.py"

# Configuration file suffixes and names indexed as `configuration` nodes.
CONFIG_SUFFIXES = frozenset({".toml", ".ini", ".cfg", ".yaml", ".yml", ".env"})
CONFIG_NAMES = frozenset({
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "requirements.txt",
    "tox.ini",
    "dockerfile",
    "docker-compose.yml",
    "makefile",
})

TEST_NAME_PREFIXES = ("test_",)
TEST_NAME_SUFFIXES = ("_test.py",)


def node_id(kind: NodeKind, *parts: str) -> str:
    """Stable, human-readable node address."""
    return f"{kind}:" + "::".join(p for p in parts if p)


def is_test_file(rel_path: str) -> bool:
    posix = rel_path.replace("\\", "/")
    name = PurePosixPath(posix).name
    if name.startswith(TEST_NAME_PREFIXES) or name.endswith(TEST_NAME_SUFFIXES):
        return True
    return any(part in ("tests", "test", "testing") for part in PurePosixPath(posix).parts[:-1])


def is_config_file(rel_path: str) -> bool:
    name = PurePosixPath(rel_path.replace("\\", "/")).name
    if name.lower() in CONFIG_NAMES:
        return True
    return PurePosixPath(name).suffix.lower() in CONFIG_SUFFIXES


def is_under(rel_path: str, prefixes: frozenset[str] | set[str]) -> bool:
    """True when a repo-relative path sits inside any of the given directories."""
    posix = rel_path.replace("\\", "/")
    return any(posix == p or posix.startswith(f"{p}/") for p in prefixes if p)


def discover_python_files(
    repo_path: Path,
    source_roots: list[str],
    exclude_dirs: frozenset[str] | set[str] | None = None,
) -> list[str]:
    """Repo-relative Python files under the source roots, tests included.

    Tests are indexed deliberately: the `tests` edge from a test to the module it
    imports is one of the more useful relations the graph carries.

    `exclude_dirs` carries repo-relative directories to skip — used to keep
    nested repositories (a vendored checkout with its own `.git`) out of the
    parent's index, since their history and ownership belong to a different
    repository entirely.
    """
    repo = repo_path.resolve()
    excluded_dirs = frozenset(exclude_dirs or ())
    found: list[str] = []

    scan_paths = resolve_scan_paths(repo, source_roots) if source_roots else [repo]
    seen: set[str] = set()

    for scan_path in scan_paths:
        if not scan_path.exists():
            continue
        candidates = [scan_path] if scan_path.is_file() else sorted(scan_path.rglob("*.py"))
        for py_file in candidates:
            if py_file.suffix != ".py":
                continue
            try:
                rel = str(py_file.relative_to(repo)).replace("\\", "/")
            except ValueError:
                continue
            if is_excluded_path(rel) or rel in seen or is_under(rel, excluded_dirs):
                continue
            seen.add(rel)
            found.append(rel)

    # Source roots exclude test directories, so sweep for them separately.
    for py_file in sorted(repo.rglob("*.py")):
        try:
            rel = str(py_file.relative_to(repo)).replace("\\", "/")
        except ValueError:
            continue
        if rel in seen or not is_test_file(rel) or is_under(rel, excluded_dirs):
            continue
        if any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in PurePosixPath(rel).parts):
            continue
        seen.add(rel)
        found.append(rel)

    return sorted(found)


def discover_config_files(repo_path: Path) -> list[str]:
    """Top-level configuration files. Depth-limited — config lives near the root."""
    repo = repo_path.resolve()
    found: list[str] = []
    if not repo.is_dir():
        return found
    for child in sorted(repo.iterdir()):
        if not child.is_file():
            continue
        rel = child.name
        if is_config_file(rel) and not rel.startswith("."):
            found.append(rel)
    return found


class RepositoryGraphBuilder:
    """Accumulates nodes and edges, deduplicating by id."""

    def __init__(self, repo_path: str, source_roots: list[str]):
        self.graph = RepositoryGraph(repo_path=repo_path, source_roots=list(source_roots))
        self._edge_keys: set[tuple[str, str, str]] = set()

    # -- primitives

    def add_node(self, node: RepoNode) -> str:
        self.graph.nodes[node.id] = node
        return node.id

    def add_edge(self, source: str, target: str, kind: EdgeKind, **attributes) -> None:
        key = (source, target, kind)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.graph.edges.append(
            RepoEdge(source=source, target=target, kind=kind, attributes=attributes)
        )

    # -- structure

    def add_repository(self) -> str:
        return self.add_node(
            RepoNode(
                id=node_id("repository", self.graph.repo_path),
                kind="repository",
                name=PurePosixPath(self.graph.repo_path).name or "repository",
            )
        )

    def add_directories(self, repo_root: str, rel_path: str, repo_path_obj: Path) -> str:
        """Create the directory/package chain for a file, returning its parent id."""
        parts = PurePosixPath(rel_path).parts[:-1]
        parent = repo_root
        walked: list[str] = []

        for part in parts:
            walked.append(part)
            directory = "/".join(walked)
            is_package = (repo_path_obj / directory / PACKAGE_MARKER).is_file()
            kind: NodeKind = "package" if is_package else "directory"
            current = node_id(kind, directory)
            if current not in self.graph.nodes:
                self.add_node(
                    RepoNode(id=current, kind=kind, name=part, file=directory, parent=parent)
                )
            self.add_edge(parent, current, "contains")
            parent = current

        return parent

    def add_file(self, rel_path: str, parent: str, parsed: ParsedModule | None) -> str:
        kind: NodeKind = "test" if is_test_file(rel_path) else "file"
        file_node = node_id("file", rel_path)
        self.add_node(
            RepoNode(
                id=file_node,
                kind=kind,
                name=PurePosixPath(rel_path).name,
                file=rel_path,
                parent=parent,
                attributes={
                    "is_test": is_test_file(rel_path),
                    "docstring": (parsed.docstring or "")[:200] if parsed else "",
                },
            )
        )
        self.add_edge(parent, file_node, "contains")
        self.graph.files.append(rel_path)
        return file_node

    def add_config_file(self, rel_path: str, parent: str) -> str:
        config_node = node_id("configuration", rel_path)
        self.add_node(
            RepoNode(
                id=config_node,
                kind="configuration",
                name=PurePosixPath(rel_path).name,
                file=rel_path,
                parent=parent,
            )
        )
        self.add_edge(parent, config_node, "contains")
        return config_node

    def add_symbols(self, rel_path: str, file_node: str, parsed: ParsedModule) -> None:
        """Classes, methods, functions and module variables for one file."""
        for span in parsed.class_spans:
            class_node = node_id("class", rel_path, span.name)
            self.add_node(
                RepoNode(
                    id=class_node,
                    kind="class",
                    name=span.name,
                    file=rel_path,
                    lineno=span.span_start,
                    end_lineno=span.end_lineno,
                    parent=file_node,
                    attributes={
                        "bases": span.bases,
                        "decorators": span.decorators,
                        "is_dataclass": span.is_dataclass,
                        "is_typed_model": span.is_typed_model,
                        "docstring": (span.docstring or "")[:200],
                    },
                )
            )
            self.add_edge(file_node, class_node, "defines")
            self.add_edge(file_node, class_node, "contains")

            # Inheritance. `implements` is reserved for ABC/Protocol-style bases,
            # which Python expresses through the same syntax as inheritance.
            for base in span.bases:
                base_node = node_id("class", rel_path, base)
                kind: EdgeKind = "implements" if base in ("ABC", "Protocol") else "inherits"
                self.add_edge(class_node, base_node, kind, unresolved=base_node not in self.graph.nodes)

        for span in parsed.function_spans:
            kind = "method" if span.is_method else "function"
            symbol_node = node_id(kind, rel_path, span.qualname)
            parent = (
                node_id("class", rel_path, span.parent_class) if span.parent_class else file_node
            )
            self.add_node(
                RepoNode(
                    id=symbol_node,
                    kind=kind,
                    name=span.name,
                    file=rel_path,
                    lineno=span.span_start,
                    end_lineno=span.end_lineno,
                    parent=parent,
                    attributes={
                        "qualname": span.qualname,
                        "is_async": span.is_async,
                        "decorators": span.decorators,
                        "calls": span.calls,
                        "docstring": (span.docstring or "")[:200],
                    },
                )
            )
            self.add_edge(parent, symbol_node, "contains")
            self.add_edge(file_node, symbol_node, "defines")

        for span in parsed.constant_spans:
            variable_node = node_id("variable", rel_path, span.name)
            self.add_node(
                RepoNode(
                    id=variable_node,
                    kind="variable",
                    name=span.name,
                    file=rel_path,
                    lineno=span.lineno,
                    end_lineno=span.end_lineno,
                    parent=file_node,
                    attributes={"annotation": span.annotation},
                )
            )
            self.add_edge(file_node, variable_node, "defines")

    def add_imports(self, rel_path: str, file_node: str, parsed: ParsedModule, module_index: dict[str, str]) -> None:
        """`imports` between files, `tests` when a test imports production code."""
        is_test = is_test_file(rel_path)

        for span in parsed.import_spans:
            if span.module:
                # `from pkg import b` names two things: the package `pkg`, and
                # possibly the submodule `pkg.b`. Resolving only the package
                # points every such import at `__init__.py` and hides real
                # module-to-module edges — including genuine import cycles.
                targets = [span.module]
                targets += [
                    f"{span.module}.{name}"
                    for name in span.names
                    if f"{span.module}.{name}" in module_index
                ]
            else:
                targets = list(span.names)

            for raw in targets:
                if not raw:
                    continue
                resolved = _resolve_module(raw, module_index)
                if resolved is None:
                    self.add_edge(
                        file_node,
                        node_id("package", raw.split(".")[0]),
                        "depends_on",
                        external=True,
                        module=raw,
                    )
                    continue

                target_node = node_id("file", resolved)
                self.add_edge(file_node, target_node, "imports", module=raw)
                if is_test and not is_test_file(resolved):
                    self.add_edge(file_node, target_node, "tests")

    def add_references(self, rel_path: str, parsed: ParsedModule, symbol_index: dict[str, str]) -> None:
        """`references` from a function to symbols it names but does not call."""
        for span in parsed.function_spans:
            kind = "method" if span.is_method else "function"
            source = node_id(kind, rel_path, span.qualname)
            for name in span.references:
                if name in span.calls:
                    continue  # a call, handled by the call graph
                target = symbol_index.get(name)
                if target and target != source:
                    self.add_edge(source, target, "references")


def _resolve_module(module: str, module_index: dict[str, str]) -> str | None:
    """Map a dotted import to a repo-relative file, longest prefix first."""
    dotted = module.replace("/", ".")
    if dotted in module_index:
        return module_index[dotted]

    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        prefix = ".".join(parts[:cut])
        if prefix in module_index:
            return module_index[prefix]

    # Bare `import auth` inside a package resolves to a unique module basename.
    tail = parts[-1]
    matches = [path for dotted_name, path in module_index.items() if dotted_name.split(".")[-1] == tail]
    if len(matches) == 1:
        return matches[0]
    return None


def build_module_index(files: list[str]) -> dict[str, str]:
    """Dotted module name -> repo-relative path, for every indexed file."""
    index: dict[str, str] = {}
    for rel in files:
        posix = rel.replace("\\", "/")
        without_suffix = posix[:-3] if posix.endswith(".py") else posix
        dotted = without_suffix.replace("/", ".")
        index.setdefault(dotted, rel)
        if dotted.endswith(".__init__"):
            index.setdefault(dotted[: -len(".__init__")], rel)
    return index


def build_repository_graph(
    repo_path: Path,
    source_roots: list[str],
    parsed_modules: dict[str, ParsedModule] | None = None,
    files: list[str] | None = None,
) -> tuple[RepositoryGraph, dict[str, ParsedModule]]:
    """Build the full graph. Reuses already-parsed modules when supplied."""
    repo = repo_path.resolve()
    file_list = files if files is not None else discover_python_files(repo, source_roots)

    modules: dict[str, ParsedModule] = dict(parsed_modules or {})
    for rel in file_list:
        if rel in modules:
            continue
        try:
            source = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parsed = parse_source(source)
        if parsed is not None:
            modules[rel] = parsed

    builder = RepositoryGraphBuilder(str(repo), source_roots)
    repo_root = builder.add_repository()
    module_index = build_module_index(file_list)

    file_nodes: dict[str, str] = {}
    for rel in file_list:
        parent = builder.add_directories(repo_root, rel, repo)
        file_nodes[rel] = builder.add_file(rel, parent, modules.get(rel))

    for rel in discover_config_files(repo):
        builder.add_config_file(rel, repo_root)

    for rel, parsed in modules.items():
        node = file_nodes.get(rel)
        if node:
            builder.add_symbols(rel, node, parsed)

    # Symbol index built after all symbols exist, so `references` can resolve
    # across files rather than only within one.
    symbol_index: dict[str, str] = {}
    for node in builder.graph.nodes.values():
        if node.kind in ("class", "function", "method", "variable"):
            symbol_index.setdefault(node.name, node.id)

    for rel, parsed in modules.items():
        node = file_nodes.get(rel)
        if node:
            builder.add_imports(rel, node, parsed, module_index)
            builder.add_references(rel, parsed, symbol_index)

    builder.graph.files = sorted(file_nodes)
    return builder.graph, modules
