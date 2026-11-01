"""Architectural hotspot detection over the knowledge graph.

Eight detectors, each deterministic, each returning the evidence that fired it.
Thresholds are named module-level constants rather than inline literals, because
every one of them is a judgement call a reader is entitled to disagree with.

Two detectors need their limits stated honestly:

**Dead module** and **unused code** detect *statically unreferenced* code, which
is not the same as unreachable code. Python resolves dynamically: a module
loaded by `importlib`, a function registered through a decorator, a plugin
discovered by entry point, or anything called only from a template or a config
string will look unreferenced here and is not. Both detectors therefore exclude
the cases they can recognise (API surface, tests, package initialisers, dunder
methods) and report what remains as a *candidate*, with severity, never as a
verdict. Deleting on this signal alone would break working repositories.

**Circular dependency** operates on file-level `IMPORTS` edges. A cycle there is
real, but Python tolerates many import cycles that resolve at runtime through
deferred imports, so a reported cycle is a design smell rather than a defect.
"""

from __future__ import annotations

from backend.models.knowledge_graph import Evidence, Explanation, Hotspot
from backend.services.knowledge_graph import RepositoryKnowledgeGraph
from backend.services.repository_graph import is_test_file

# -- thresholds ------------------------------------------------------------

# A file defining this many callables and classes is doing too much.
GOD_OBJECT_SYMBOLS = 25
GOD_OBJECT_COMPLEXITY = 60          # summed cyclomatic complexity
GOD_OBJECT_CLASS_METHODS = 20       # a single class with this many methods

# Imported by this many modules while itself being simple: a utility everything
# is coupled to. Not a defect, but a change here is repository-wide.
CENTRAL_UTILITY_FAN_IN = 10
CENTRAL_UTILITY_MAX_COMPLEXITY = 8

# Cycles longer than this are reported truncated; the head is what matters.
MAX_CYCLE_LENGTH = 12
MAX_CYCLES_REPORTED = 20

# Files under these names are import side-effect surfaces, never dead.
ALWAYS_LIVE_BASENAMES = frozenset({
    "__init__.py", "__main__.py", "conftest.py", "setup.py", "manage.py", "wsgi.py", "asgi.py",
})

# Methods Python itself calls. Zero static callers proves nothing about them.
DUNDER_PREFIX = "__"


def _evidence(signal: str, value: float, detail: str, provenance: str, edges=None) -> Evidence:
    return Evidence(
        signal=signal,
        value=value,
        contribution=value,
        detail=detail,
        provenance=provenance,  # type: ignore[arg-type]
        edges=edges or [],
    )


# ============================================================ detectors


def find_god_objects(graph: RepositoryKnowledgeGraph) -> list[Hotspot]:
    """Files or classes concentrating too much behaviour."""
    hotspots: list[Hotspot] = []

    for node in graph.nodes_of_type("file"):
        if is_test_file(node.file):
            continue
        parsed = graph.intelligence.parsed_modules.get(node.file)
        if parsed is None:
            continue

        symbols = len(parsed.function_spans) + len(parsed.class_spans)
        total_complexity = sum(s.complexity for s in parsed.function_spans)
        if symbols < GOD_OBJECT_SYMBOLS and total_complexity < GOD_OBJECT_COMPLEXITY:
            continue

        severity = round(
            min(1.0, max(symbols / (GOD_OBJECT_SYMBOLS * 2), total_complexity / (GOD_OBJECT_COMPLEXITY * 2))),
            4,
        )
        hotspots.append(
            Hotspot(
                kind="god_object",
                target=node.file,
                severity=severity,
                summary=f"{node.file} defines {symbols} symbol(s) with total complexity {total_complexity}",
                members=sorted(s.qualname for s in parsed.function_spans)[:20],
                explanation=Explanation(
                    summary="a single module concentrating behaviour that belongs in several",
                    evidence=[
                        _evidence("symbol_count", float(symbols), f"{symbols} functions and classes defined", "repository_graph"),
                        _evidence("total_complexity", float(total_complexity), f"summed cyclomatic complexity {total_complexity}", "repository_graph"),
                    ],
                ),
            )
        )

    for node in graph.nodes_of_type("class"):
        methods = graph.out_edges(node.id, "CONTAINS")
        if len(methods) < GOD_OBJECT_CLASS_METHODS:
            continue
        hotspots.append(
            Hotspot(
                kind="god_object",
                target=f"{node.file}::{node.name}",
                severity=round(min(1.0, len(methods) / (GOD_OBJECT_CLASS_METHODS * 2)), 4),
                summary=f"class {node.name} defines {len(methods)} methods",
                members=sorted(graph.nodes[e.target].qualname for e in methods)[:20],
                explanation=Explanation(
                    summary="a single class with an unusually broad interface",
                    evidence=[
                        _evidence(
                            "method_count",
                            float(len(methods)),
                            f"{len(methods)} methods contained",
                            "repository_graph",
                            [f"{node.id}->{e.target}" for e in methods[:5]],
                        )
                    ],
                ),
            )
        )

    hotspots.sort(key=lambda h: (-h.severity, h.target))
    return hotspots


def find_circular_dependencies(graph: RepositoryKnowledgeGraph) -> list[Hotspot]:
    """Import cycles between files, found by iterative depth-first search."""
    adjacency: dict[str, list[str]] = {}
    for node in graph.file_nodes():
        adjacency[node.id] = sorted(
            e.target for e in graph.out_edges(node.id, "IMPORTS") if e.target in graph.nodes
        )

    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()
    colour: dict[str, int] = {}  # 0 unvisited, 1 on stack, 2 done

    for root in sorted(adjacency):
        if colour.get(root, 0) != 0:
            continue

        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = [root]
        colour[root] = 1

        while stack:
            current, index = stack[-1]
            neighbours = adjacency.get(current, [])
            if index >= len(neighbours):
                colour[current] = 2
                stack.pop()
                path.pop()
                continue

            stack[-1] = (current, index + 1)
            neighbour = neighbours[index]
            state = colour.get(neighbour, 0)

            if state == 1:  # back edge: everything from `neighbour` onward is a cycle
                start = path.index(neighbour)
                cycle = path[start:]
                key = _canonical_cycle(cycle)
                if key not in seen_cycles and len(cycle) > 1:
                    seen_cycles.add(key)
                    cycles.append(cycle)
            elif state == 0:
                colour[neighbour] = 1
                path.append(neighbour)
                stack.append((neighbour, 0))

    hotspots: list[Hotspot] = []
    for cycle in cycles[:MAX_CYCLES_REPORTED]:
        files = [graph.nodes[i].file for i in cycle][:MAX_CYCLE_LENGTH]
        hotspots.append(
            Hotspot(
                kind="circular_dependency",
                target=files[0],
                severity=round(min(1.0, len(files) / 6.0), 4),
                summary=" -> ".join(files + [files[0]]),
                members=files,
                explanation=Explanation(
                    summary=f"{len(files)} file(s) import each other in a closed loop",
                    evidence=[
                        _evidence(
                            "cycle_length",
                            float(len(files)),
                            "import cycle: " + " -> ".join(files + [files[0]]),
                            "repository_graph",
                            [f"{cycle[i]}->{cycle[(i + 1) % len(cycle)]}" for i in range(len(cycle))],
                        )
                    ],
                ),
            )
        )

    hotspots.sort(key=lambda h: (-h.severity, h.target))
    return hotspots


def _canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Rotation-invariant key, so one cycle is not reported once per member."""
    if not cycle:
        return ()
    pivot = cycle.index(min(cycle))
    return tuple(cycle[pivot:] + cycle[:pivot])


def find_over_centralized_utilities(graph: RepositoryKnowledgeGraph) -> list[Hotspot]:
    """Simple modules that most of the repository depends on."""
    hotspots: list[Hotspot] = []

    for node in graph.nodes_of_type("file"):
        if is_test_file(node.file):
            continue
        importers = [e.source for e in graph.in_edges(node.id, "IMPORTS")]
        if len(importers) < CENTRAL_UTILITY_FAN_IN:
            continue

        parsed = graph.intelligence.parsed_modules.get(node.file)
        peak = max((s.complexity for s in parsed.function_spans), default=0) if parsed else 0
        if peak > CENTRAL_UTILITY_MAX_COMPLEXITY:
            continue  # complex and central is a god object, reported separately

        hotspots.append(
            Hotspot(
                kind="over_centralized_utility",
                target=node.file,
                severity=round(min(1.0, len(importers) / (CENTRAL_UTILITY_FAN_IN * 3)), 4),
                summary=f"{node.file} is imported by {len(importers)} module(s) with peak complexity {peak}",
                members=sorted(graph.nodes[i].file for i in importers)[:20],
                explanation=Explanation(
                    summary="a simple module the whole repository is coupled to",
                    evidence=[
                        _evidence(
                            "importer_count",
                            float(len(importers)),
                            f"imported by {len(importers)} module(s)",
                            "repository_graph",
                            [f"{i}->{node.id}" for i in importers[:5]],
                        ),
                        _evidence("peak_complexity", float(peak), f"peak cyclomatic complexity {peak}", "repository_graph"),
                    ],
                ),
            )
        )

    hotspots.sort(key=lambda h: (-h.severity, h.target))
    return hotspots


def find_dead_modules(graph: RepositoryKnowledgeGraph) -> list[Hotspot]:
    """Files nothing statically imports, tests, or documents.

    A *candidate*, not a verdict — see the module docstring on dynamic imports.
    """
    hotspots: list[Hotspot] = []

    for node in graph.nodes_of_type("file"):
        file = node.file
        if is_test_file(file) or file.rsplit("/", 1)[-1] in ALWAYS_LIVE_BASENAMES:
            continue
        if graph.in_edges(node.id, "IMPORTS") or graph.in_edges(node.id, "TESTS"):
            continue
        if any(n.type == "api" for n in graph.nodes_in_file(file)):
            continue  # externally reachable by route, not by import

        documented = bool(graph.in_edges(node.id, "DESCRIBES"))
        severity = 0.5 if documented else 0.8
        hotspots.append(
            Hotspot(
                kind="dead_module",
                target=file,
                severity=severity,
                summary=f"{file} is not imported, tested, or exposed as an API",
                explanation=Explanation(
                    summary="statically unreferenced — verify dynamic imports before removing",
                    evidence=[
                        _evidence("inbound_imports", 0.0, "no IMPORTS edge targets this file", "repository_graph"),
                        _evidence("api_surface", 0.0, "no route-decorated callable defined here", "repository_graph"),
                        _evidence(
                            "documented",
                            1.0 if documented else 0.0,
                            "documentation references it" if documented else "no documentation references it",
                            "documentation",
                        ),
                    ],
                ),
            )
        )

    hotspots.sort(key=lambda h: (-h.severity, h.target))
    return hotspots


def find_orphan_files(graph: RepositoryKnowledgeGraph) -> list[Hotspot]:
    """Files with no relation to anything but their own directory."""
    hotspots: list[Hotspot] = []
    structural = {"CONTAINS", "DEFINES"}

    for node in graph.file_nodes():
        connections = [
            e for e in graph.out_edges(node.id) + graph.in_edges(node.id)
            if e.type not in structural
        ]
        if connections:
            continue
        hotspots.append(
            Hotspot(
                kind="orphan_file",
                target=node.file,
                severity=0.6,
                summary=f"{node.file} has no imports, calls, tests, owners, or documentation",
                explanation=Explanation(
                    summary="entirely disconnected from the rest of the repository graph",
                    evidence=[
                        _evidence("non_structural_edges", 0.0, "only containment edges attach this file", "repository_graph")
                    ],
                ),
            )
        )

    hotspots.sort(key=lambda h: h.target)
    return hotspots


def find_unowned_modules(graph: RepositoryKnowledgeGraph) -> list[Hotspot]:
    """Files no author owns — nobody's git history claims them."""
    hotspots: list[Hotspot] = []

    for node in graph.file_nodes():
        if graph.in_edges(node.id, "OWNS"):
            continue
        evolution = graph.intelligence.history.evolution.get(node.file)
        # No history at all usually means the file is newer than the indexing
        # window, which is different from being abandoned.
        severity = 0.7 if evolution else 0.4
        hotspots.append(
            Hotspot(
                kind="unowned_module",
                target=node.file,
                severity=severity,
                summary=f"{node.file} has no attributed owner",
                explanation=Explanation(
                    summary="no author holds primary or secondary ownership",
                    evidence=[
                        _evidence("owns_edges", 0.0, "no OWNS edge targets this file", "ownership"),
                        _evidence(
                            "commit_history",
                            float(evolution.commit_count) if evolution else 0.0,
                            f"{evolution.commit_count} commit(s) in the indexed window" if evolution
                            else "no commits in the indexed window",
                            "history",
                        ),
                    ],
                ),
            )
        )

    hotspots.sort(key=lambda h: (-h.severity, h.target))
    return hotspots


def find_high_risk_apis(graph: RepositoryKnowledgeGraph, min_risk: float = 0.4) -> list[Hotspot]:
    """Externally reachable endpoints defined in risky modules."""
    from backend.services.risk_engine import assess_file

    hotspots: list[Hotspot] = []
    scored: dict[str, float] = {}

    for node in graph.nodes_of_type("api"):
        if node.file not in scored:
            scored[node.file] = assess_file(graph, node.file).risk
        risk = scored[node.file]
        if risk < min_risk:
            continue

        assessment = assess_file(graph, node.file)
        hotspots.append(
            Hotspot(
                kind="high_risk_api",
                target=f"{node.file}::{node.qualname}",
                severity=risk,
                summary=f"endpoint {node.qualname} is defined in a {assessment.band}-risk module",
                explanation=Explanation(
                    summary=f"externally reachable code in a module scoring {risk:.2f}",
                    evidence=assessment.evidence,
                ),
            )
        )

    hotspots.sort(key=lambda h: (-h.severity, h.target))
    return hotspots


def find_unused_code(graph: RepositoryKnowledgeGraph) -> list[Hotspot]:
    """Callables with no static caller, test, or route.

    A *candidate*, not a verdict — decorator registration and dynamic dispatch
    both produce false positives here, which is why dunders, API-backed
    callables and tests are excluded outright.
    """
    hotspots: list[Hotspot] = []
    exposed = {e.target for e in graph.edges if e.type == "EXPOSES"}

    for node_type in ("function", "method"):
        for node in graph.nodes_of_type(node_type):
            if is_test_file(node.file) or node.name.startswith(DUNDER_PREFIX):
                continue
            if node.id in exposed:
                continue
            if graph.in_edges(node.id, "CALLS") or graph.in_edges(node.id, "VALIDATES"):
                continue
            if node.attributes.get("decorators"):
                continue  # registered somewhere the AST cannot see

            hotspots.append(
                Hotspot(
                    kind="unused_code",
                    target=f"{node.file}::{node.qualname}",
                    severity=0.4 if node.name.startswith("_") else 0.55,
                    summary=f"{node.qualname}() has no static caller",
                    explanation=Explanation(
                        summary="statically uncalled — verify dynamic dispatch before removing",
                        evidence=[
                            _evidence("inbound_calls", 0.0, "no CALLS edge targets this callable", "call_graph"),
                            _evidence("validating_tests", 0.0, "no test exercises it", "call_graph"),
                        ],
                    ),
                )
            )

    hotspots.sort(key=lambda h: (-h.severity, h.target))
    return hotspots


DETECTORS = (
    find_god_objects,
    find_circular_dependencies,
    find_over_centralized_utilities,
    find_dead_modules,
    find_orphan_files,
    find_unowned_modules,
    find_high_risk_apis,
    find_unused_code,
)


def analyze_architecture(
    graph: RepositoryKnowledgeGraph,
    limit_per_kind: int = 10,
) -> list[Hotspot]:
    """Run every detector, capped per kind so one finding class cannot flood."""
    findings: list[Hotspot] = []
    for detector in DETECTORS:
        findings.extend(detector(graph)[:limit_per_kind])
    findings.sort(key=lambda h: (-h.severity, h.kind, h.target))
    return findings


def explain_hotspot(hotspot: Hotspot) -> dict:
    """Flatten a hotspot into the mandatory why/signals/edges/evidence shape."""
    return {
        "kind": hotspot.kind,
        "target": hotspot.target,
        "severity": hotspot.severity,
        "why": hotspot.explanation.summary or hotspot.summary,
        "signals": hotspot.explanation.signals,
        "edges": hotspot.explanation.edges,
        "evidence": [e.describe() for e in hotspot.explanation.evidence],
        "members": hotspot.members,
    }
