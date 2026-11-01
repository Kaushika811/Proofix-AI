"""Multi-origin blast graph traversal with hop-aware propagation confidence."""

from __future__ import annotations

from backend.models.blast import BlastEdge, BlastGraphResult, EdgeBasis, ScopedFile
from backend.models.sig import SemanticIntentGraph
from backend.services.repo_layout import is_vendor_path

CONFIDENCE_THRESHOLD = 0.7
MAX_HOPS = 3
HOP_DECAY = 0.85


def resolve_origins(citations: list[dict]) -> list[str]:
    """Blast origins from A4's citations — the fallback path taken when
    `resolve_patch_target` found no application-scoped target of its own.

    A citation naming a file under `.venv`, `node_modules`, `site-packages`,
    `.git`, or a build/cache directory is real evidence about *something* —
    bandit really did find a weak hash inside an installed dependency — but
    it is not evidence about this repository's own code, and must never
    become a blast origin (and from there, a repair target). Without this
    filter, the only citation available for a bug whose evidence happens to
    sit inside a vendored dependency silently became the resolved patch
    target. An empty result here is the honest answer: no application-scoped
    origin was found, not a guess dressed up as one.
    """
    application = [c for c in citations if c.get("file") and not is_vendor_path(str(c["file"]))]
    verified = [c["file"] for c in application if c.get("verified")]
    if verified:
        return list(dict.fromkeys(verified))
    fallback = [c["file"] for c in application]
    return list(dict.fromkeys(fallback))


def traverse_multi_origin(
    sig: SemanticIntentGraph,
    origins: list[str],
    max_hops: int = MAX_HOPS,
) -> BlastGraphResult:
    if not origins:
        return BlastGraphResult()

    merged: dict[str, ScopedFile] = {}
    auto_patch: set[str] = set()
    human_review: set[str] = set()
    directions_by_path: dict[str, set[str]] = {}
    edges: list[BlastEdge] = []

    for origin in origins:
        if origin not in sig.files:
            continue
        _bfs_from_origin(
            sig, origin, max_hops, merged, auto_patch, human_review, directions_by_path, edges
        )

    # Direction is accumulated globally (`directions_by_path`) because the same
    # file can be reached both ways, or reached from more than one origin — the
    # per-hop winner logic below only tracks which entry scored highest, and
    # would otherwise silently drop whichever direction lost that comparison.
    for path, scoped in merged.items():
        scoped.directions = sorted(directions_by_path.get(path, {scoped.direction}))

    scope = sorted(merged.values(), key=lambda s: (s.hop_count, s.path))
    return BlastGraphResult(
        scope=scope,
        human_review_required=sorted(human_review),
        auto_patch_scope=sorted(auto_patch),
        origins=origins,
        edges=edges,
    )


def _bfs_from_origin(
    sig: SemanticIntentGraph,
    origin: str,
    max_hops: int,
    merged: dict[str, ScopedFile],
    auto_patch: set[str],
    human_review: set[str],
    directions_by_path: dict[str, set[str]],
    edges: list[BlastEdge],
) -> None:
    # Queue items carry the parent path and edge basis that produced them, so
    # every non-origin file can record how it was actually reached
    # (`BlastEdge`) rather than only its final hop count.
    queue: list[tuple[str, str, int, str | None, EdgeBasis | None]] = [
        (origin, "forward", 0, None, None),
        (origin, "backward", 0, None, None),
    ]
    visited: set[tuple[str, str, int]] = set()

    while queue:
        path, direction, hops, parent, basis = queue.pop(0)
        key = (path, direction, hops)
        if key in visited or hops > max_hops:
            continue
        visited.add(key)

        node = sig.files.get(path)
        if not node:
            continue

        directions_by_path.setdefault(path, set()).add(direction)

        if parent is not None and basis is not None:
            edges.append(
                BlastEdge(
                    from_path=parent, to_path=path, direction=direction, basis=basis,
                    hop_count=hops,
                )
            )

        security_score = 0.8 if node.role in ("auth-boundary", "public-api") else 0.5
        risk = node.criticality * node.churn_weight * security_score
        base_confidence = min(1.0, node.criticality * 0.6 + node.churn_weight * 0.4)
        propagation = base_confidence * (HOP_DECAY**hops)

        scoped = ScopedFile(
            path=path,
            direction=direction,
            propagation_confidence=round(propagation, 4),
            risk_score=round(risk, 4),
            hop_count=hops,
            origin=origin,
            reached_via=parent,
            edge_basis=basis,
        )
        existing = merged.get(path)
        if existing is None or scoped.propagation_confidence > existing.propagation_confidence:
            merged[path] = scoped
        elif existing and scoped.hop_count < existing.hop_count:
            merged[path] = scoped

        if propagation >= CONFIDENCE_THRESHOLD:
            auto_patch.add(path)
        else:
            human_review.add(path)

        if hops >= max_hops:
            continue

        if direction == "forward":
            for edge in sig.edges:
                if edge[0] == path:
                    target, edge_basis = _module_to_file(sig, edge[1])
                    if target:
                        queue.append((target, "forward", hops + 1, path, edge_basis))
        else:
            for other_path, other_node in sig.files.items():
                for imp in other_node.imports:
                    matched, edge_basis = _matches(path, imp)
                    if matched:
                        queue.append((other_path, "backward", hops + 1, path, edge_basis))


def _module_to_file(sig: SemanticIntentGraph, module: str) -> tuple[str | None, EdgeBasis | None]:
    # Precise branch first: the import string names the tail of a real file
    # path, which cannot false-positive the way plain containment can.
    for path in sig.files:
        if path.endswith(f"{module}.py"):
            return path, "resolved_suffix"
    for path in sig.files:
        if module in path:
            return path, "name_contains"
    return None, None


def _matches(path: str, imp: str) -> tuple[bool, EdgeBasis | None]:
    stem = path.replace("/", ".").replace(".py", "")
    if stem.endswith(imp):
        return True, "resolved_suffix"
    if imp in stem:
        return True, "name_contains"
    return False, None
