"""Cross-run SIG cache for A1 (roles + parsed module metadata)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from backend.models.sig import FileRole
from backend.services.ast_import_graph import ImportGraph
from backend.services.git_service import get_head_sha, get_worktree_diff_hash
from backend.services.python_ast_parser import ParsedModule
from backend.services.repo_layout import is_excluded_path, is_production_file, resolve_scan_paths
from backend.services.role_classifier import RolePrediction, RoleSource


class CachedFileNode(BaseModel):
    role: FileRole
    role_source: RoleSource
    imports: list[str] = Field(default_factory=list)
    imported_by: list[str] = Field(default_factory=list)
    exported_symbols: list[str] = Field(default_factory=list)
    parsed_module: ParsedModule


class CachedSIGPayload(BaseModel):
    source_roots: list[str] = Field(default_factory=list)
    files: dict[str, CachedFileNode] = Field(default_factory=dict)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    cached_at: datetime = Field(default_factory=datetime.utcnow)


def cache_key(version: str, repo_hash: str) -> str:
    return f"sig_cache:{version}:{repo_hash}"


def compute_repo_hash(repo_path: Path, source_roots: list[str]) -> str:
    repo_path = repo_path.resolve()
    head = get_head_sha(repo_path)
    if head:
        roots_key = "|".join(sorted(source_roots))
        diff_hash = get_worktree_diff_hash(repo_path)
        raw = f"{head}|{diff_hash}|{roots_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    parts: list[str] = []
    for rel in _production_file_paths(repo_path, source_roots):
        full = repo_path / rel
        try:
            content = full.read_text(encoding="utf-8")
        except OSError:
            continue
        parts.append(f"{rel}:{hashlib.sha256(content.encode()).hexdigest()}")
    roots_key = "|".join(sorted(source_roots))
    raw = f"{roots_key}|" + "|".join(sorted(parts))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _production_file_paths(repo_path: Path, source_roots: list[str]) -> list[str]:
    paths: list[str] = []
    for scan_path in resolve_scan_paths(repo_path, source_roots):
        if not scan_path.exists():
            continue
        if scan_path.is_file() and scan_path.suffix == ".py":
            candidates = [scan_path]
        else:
            candidates = list(scan_path.rglob("*.py"))
        for py_file in candidates:
            rel = str(py_file.relative_to(repo_path))
            if is_excluded_path(rel):
                continue
            if is_production_file(rel, source_roots):
                paths.append(rel)
    return sorted(paths)


def build_cache_payload(
    source_roots: list[str],
    graph: ImportGraph,
    roles: dict[str, RolePrediction],
    parsed_modules: dict[str, ParsedModule],
    imported_by_map: dict[str, list[str]],
) -> CachedSIGPayload:
    files: dict[str, CachedFileNode] = {}
    for path, module in parsed_modules.items():
        pred = roles[path]
        files[path] = CachedFileNode(
            role=pred.role,
            role_source=pred.role_source,
            imports=graph.files.get(path, module.imports),
            imported_by=imported_by_map.get(path, []),
            exported_symbols=module.exported_symbols,
            parsed_module=module,
        )
    return CachedSIGPayload(
        source_roots=source_roots,
        files=files,
        edges=graph.edges,
        cached_at=datetime.utcnow(),
    )


def payload_to_roles(payload: CachedSIGPayload) -> dict[str, RolePrediction]:
    return {
        path: RolePrediction(role=node.role, confidence=1.0, role_source=node.role_source)
        for path, node in payload.files.items()
    }


def payload_to_parsed(payload: CachedSIGPayload) -> dict[str, ParsedModule]:
    return {path: node.parsed_module for path, node in payload.files.items()}


def payload_to_graph(payload: CachedSIGPayload) -> ImportGraph:
    files = {path: node.imports for path, node in payload.files.items()}
    return ImportGraph(files=files, edges=payload.edges)


def serialize_payload(payload: CachedSIGPayload) -> str:
    return payload.model_dump_json()


def deserialize_payload(raw: str) -> CachedSIGPayload:
    return CachedSIGPayload.model_validate_json(raw)
