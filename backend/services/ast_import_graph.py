from pathlib import Path

from pydantic import BaseModel, Field

from backend.services.python_ast_parser import ParsedModule, parse_python_file
from backend.services.repo_layout import is_excluded_path, is_production_file, resolve_scan_paths


class ImportGraph(BaseModel):
    files: dict[str, list[str]] = Field(default_factory=dict)
    edges: list[tuple[str, str]] = Field(default_factory=list)


def _iter_production_py_files(repo_path: Path, source_roots: list[str] | None) -> list[str]:
    repo_path = repo_path.resolve()
    if source_roots is not None:
        scan_paths = resolve_scan_paths(repo_path, source_roots)
        py_files: list[Path] = []
        for scan_path in scan_paths:
            if not scan_path.exists():
                continue
            if scan_path.is_file() and scan_path.suffix == ".py":
                py_files.append(scan_path)
            else:
                py_files.extend(scan_path.rglob("*.py"))
    else:
        py_files = list(repo_path.rglob("*.py"))

    rel_paths: list[str] = []
    for py_file in py_files:
        rel = str(py_file.relative_to(repo_path))
        if is_excluded_path(rel):
            continue
        if source_roots is not None and not is_production_file(rel, source_roots):
            continue
        rel_paths.append(rel)
    return rel_paths


def import_graph_from_parsed(parsed_modules: dict[str, ParsedModule]) -> ImportGraph:
    files: dict[str, list[str]] = {}
    edges: list[tuple[str, str]] = []
    for rel, module in parsed_modules.items():
        imports = list(module.imports)
        files[rel] = imports
        for imp in imports:
            edges.append((rel, imp))
    return ImportGraph(files=files, edges=edges)


def build_import_graph(
    repo_path: Path,
    source_roots: list[str] | None = None,
) -> tuple[ImportGraph, dict[str, ParsedModule]]:
    """Scan production files, parse each once, return import graph + parsed modules."""
    repo_path = repo_path.resolve()
    parsed_modules: dict[str, ParsedModule] = {}

    for rel in _iter_production_py_files(repo_path, source_roots):
        module = parse_python_file(repo_path, rel)
        if module is not None:
            parsed_modules[rel] = module

    graph = import_graph_from_parsed(parsed_modules)
    return graph, parsed_modules


def build_import_graph_reusing(
    repo_path: Path,
    source_roots: list[str] | None,
    cached_modules: dict[str, ParsedModule] | None,
) -> tuple[ImportGraph, dict[str, ParsedModule], int]:
    """`build_import_graph`, but taking already-parsed modules where available.

    The file list, its order, and each `ParsedModule` are exactly what
    `build_import_graph` would produce — the same parser ran over the same bytes
    — so the resulting graph is identical and only the parse is skipped. A cache
    miss on any file falls through to parsing it, so a partial or stale supply
    degrades to the original behaviour rather than to a wrong graph.

    Returns the graph, the parsed modules, and how many parses were avoided.
    """
    repo_path = repo_path.resolve()
    supplied = cached_modules or {}
    parsed_modules: dict[str, ParsedModule] = {}
    reused = 0

    for rel in _iter_production_py_files(repo_path, source_roots):
        module = supplied.get(rel) or supplied.get(rel.replace("\\", "/"))
        if module is not None:
            reused += 1
        else:
            module = parse_python_file(repo_path, rel)
        if module is not None:
            parsed_modules[rel] = module

    return import_graph_from_parsed(parsed_modules), parsed_modules, reused


def module_to_files(import_map: dict[str, list[str]], module: str) -> list[str]:
    """Find files that import a given module name."""
    result = []
    module_lower = module.lower().replace("-", "_")
    for path, imports in import_map.items():
        for imp in imports:
            if imp.lower().replace("-", "_") == module_lower:
                result.append(path)
    return result


ROLE_CRITICALITY = {
    "auth-boundary": 0.95,
    "public-api": 0.85,
    "data-access": 0.80,
    "config-surface": 0.75,
    "internal-util": 0.40,
    "test-only": 0.10,
}


def compute_criticality(role: str, churn_weight: float) -> float:
    base = ROLE_CRITICALITY.get(role, 0.4)
    return min(1.0, base * 0.7 + churn_weight * 0.3)
