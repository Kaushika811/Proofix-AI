"""The Repository Knowledge Graph: one connected view over six analyses.

This module does two things and deliberately nothing else.

**Adapters.** Each of the six existing structures is walked once and projected
into typed nodes and edges. No structure is rebuilt and no analysis is repeated:
`RepositoryGraph` already knows what contains what, `CallGraph` already resolved
the calls, `GitHistoryGraph` already walked the log. The adapters translate ids
and attach provenance. Where an adapter can only produce a node reference, it
produces a reference — the substance stays in the source structure, which
remains the single place it is maintained.

**Traversal.** A deterministic query engine over the resulting adjacency. Every
query returns results in a total order, so two calls on the same graph produce
the same answer in the same sequence.

The edges worth the effort are the cross-structure ones, which no single
analysis could produce alone:

    Commit      MODIFIED   Function     (history × repository graph, by span)
    Document    DESCRIBES  Function     (documentation × repository graph)
    Repair      FIXED      Function     (repair memory × repository graph)
    Test        VALIDATES  Function     (call graph × test file classification)
    Owner       OWNS       File         (ownership × repository graph)

`MODIFIED` is span-based: a commit touching a file is attributed to the specific
functions whose line ranges it overlaps only when the history provides line
information. Git history here records file-level changes, so the edge is
attributed at file level and the function-level edge is emitted only through
`CONTAINS`, never invented. Guessing which function a commit changed would put a
fabricated edge under a risk score, which is exactly what this layer must not do.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from backend.models.knowledge_graph import (
    KGEdge,
    KGNode,
    KnowledgeGraphMetrics,
    NodeType,
)
from backend.models.repository_graph import RepositoryIntelligence
from backend.services.call_graph import graph_file_of
from backend.services.repository_graph import is_test_file, node_id

# Traversal ceilings. A pathological graph must not be able to hang a run.
MAX_TRAVERSAL_NODES = 20_000
DEFAULT_HOPS = 2

# Route-decorator names that mark a callable as an externally reachable API.
API_DECORATORS = frozenset({
    "route", "get", "post", "put", "patch", "delete", "head", "options",
    "websocket", "api_route", "endpoint", "command", "task", "handler",
    "app", "router", "grpc", "rpc",
})


def owner_id(author: str) -> str:
    return f"owner:{author}"


def commit_id(sha: str) -> str:
    return f"commit:{sha}"


def document_id(path: str, position: int) -> str:
    return f"document:{path}#{position}"


def repair_id(value: str) -> str:
    return f"repair:{value}"


def capability_id(slug: str) -> str:
    return f"capability:{slug}"


def callable_node_id(file: str, qualname: str, is_method: bool) -> str:
    """Map a `CallGraph` id onto the `RepositoryGraph` node id for the same symbol."""
    return node_id("method" if is_method else "function", file, qualname)


def _split_callable(identifier: str) -> tuple[str, str]:
    file = graph_file_of(identifier)
    return file, identifier.split("::", 1)[1] if "::" in identifier else ""


@dataclass
class _Adjacency:
    """Directed index with a reverse view, built once per graph."""

    out: dict[str, list[int]] = field(default_factory=dict)
    inc: dict[str, list[int]] = field(default_factory=dict)


class RepositoryKnowledgeGraph:
    """A connected, queryable view over `RepositoryIntelligence`.

    Construct via `build_knowledge_graph`. The graph holds a reference to the
    intelligence it was built from, so queries that need detail the graph does
    not carry (a function's source span, a repair's full record) read it from
    there rather than from a second copy.
    """

    def __init__(self, intelligence: RepositoryIntelligence):
        self.intelligence = intelligence
        self.nodes: dict[str, KGNode] = {}
        self.edges: list[KGEdge] = []
        self.metrics = KnowledgeGraphMetrics()
        self._adjacency = _Adjacency()
        self._edge_keys: set[tuple[str, str, str]] = set()
        self._by_type: dict[str, list[str]] = {}
        self._by_file: dict[str, list[str]] = {}

    # -- construction ----------------------------------------------------

    def add_node(self, node: KGNode) -> str:
        if node.id not in self.nodes:
            self.nodes[node.id] = node
            self._by_type.setdefault(node.type, []).append(node.id)
            if node.file:
                self._by_file.setdefault(node.file, []).append(node.id)
        return node.id

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        *,
        weight: float = 1.0,
        provenance: str = "repository_graph",
        evidence: str = "",
    ) -> None:
        """Add an edge, ignoring duplicates and edges to nodes that do not exist.

        Dropping dangling edges rather than creating placeholder nodes keeps the
        node set honest: every node in this graph corresponds to something the
        underlying analyses actually found.
        """
        if source not in self.nodes or target not in self.nodes:
            return
        key = (source, target, edge_type)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)

        position = len(self.edges)
        self.edges.append(
            KGEdge(
                source=source,
                target=target,
                type=edge_type,  # type: ignore[arg-type]
                weight=weight,
                provenance=provenance,  # type: ignore[arg-type]
                evidence=evidence,
            )
        )
        self._adjacency.out.setdefault(source, []).append(position)
        self._adjacency.inc.setdefault(target, []).append(position)

    # -- access ----------------------------------------------------------

    def node(self, node_ref: str) -> KGNode | None:
        return self.nodes.get(node_ref)

    def nodes_of_type(self, node_type: NodeType) -> list[KGNode]:
        return [self.nodes[i] for i in self._by_type.get(node_type, [])]

    def nodes_in_file(self, file: str) -> list[KGNode]:
        return [self.nodes[i] for i in self._by_file.get(file, [])]

    def file_nodes(self) -> list[KGNode]:
        """Every source file, production and test.

        Test modules carry the `test` type rather than `file`, so any caller
        that means "all source files" must ask for both. Getting this wrong is
        silent — the query simply returns fewer results — which is why it is a
        method rather than a convention.
        """
        return self.nodes_of_type("file") + self.nodes_of_type("test")

    def out_edges(self, node_ref: str, edge_type: str | None = None) -> list[KGEdge]:
        edges = [self.edges[i] for i in self._adjacency.out.get(node_ref, [])]
        return [e for e in edges if edge_type is None or e.type == edge_type]

    def in_edges(self, node_ref: str, edge_type: str | None = None) -> list[KGEdge]:
        edges = [self.edges[i] for i in self._adjacency.inc.get(node_ref, [])]
        return [e for e in edges if edge_type is None or e.type == edge_type]

    def neighbors(
        self,
        node_ref: str,
        edge_type: str | None = None,
        direction: str = "out",
    ) -> list[str]:
        if direction == "out":
            return [e.target for e in self.out_edges(node_ref, edge_type)]
        if direction == "in":
            return [e.source for e in self.in_edges(node_ref, edge_type)]
        return [e.target for e in self.out_edges(node_ref, edge_type)] + [
            e.source for e in self.in_edges(node_ref, edge_type)
        ]

    def degree(self, node_ref: str) -> int:
        return len(self._adjacency.out.get(node_ref, [])) + len(
            self._adjacency.inc.get(node_ref, [])
        )

    def traverse(
        self,
        start: str,
        edge_types: tuple[str, ...] | None = None,
        hops: int = DEFAULT_HOPS,
        direction: str = "out",
    ) -> dict[str, int]:
        """Breadth-first reachable set, mapping node id to hop distance.

        The start node is included at distance 0. Bounded by MAX_TRAVERSAL_NODES
        so a dense graph cannot stall a query.
        """
        if start not in self.nodes:
            return {}

        seen: dict[str, int] = {start: 0}
        queue: deque[tuple[str, int]] = deque([(start, 0)])

        while queue and len(seen) < MAX_TRAVERSAL_NODES:
            current, distance = queue.popleft()
            if distance >= hops:
                continue
            candidates: list[str] = []
            for edge_type in edge_types or (None,):
                candidates.extend(self.neighbors(current, edge_type, direction))
            for neighbor in candidates:
                if neighbor not in seen:
                    seen[neighbor] = distance + 1
                    queue.append((neighbor, distance + 1))

        return seen

    # -- metrics ---------------------------------------------------------

    def finalize_metrics(self, build_ms: int) -> None:
        self.metrics.node_count = len(self.nodes)
        self.metrics.edge_count = len(self.edges)
        self.metrics.build_ms = build_ms
        self.metrics.nodes_by_type = {
            node_type: len(ids) for node_type, ids in sorted(self._by_type.items())
        }

        by_edge: dict[str, int] = {}
        for edge in self.edges:
            by_edge[edge.type] = by_edge.get(edge.type, 0) + 1
        self.metrics.edges_by_type = dict(sorted(by_edge.items()))

        if self.nodes:
            # Directed graph: each edge contributes to one out-degree and one
            # in-degree, so average total degree is 2E/V.
            self.metrics.average_degree = round(2 * len(self.edges) / len(self.nodes), 4)

        files_total = len(self.intelligence.repository_graph.files)
        represented = len({n.file for n in self.nodes.values() if n.file})
        self.metrics.files_total = files_total
        self.metrics.files_represented = represented
        self.metrics.repository_coverage = (
            round(min(1.0, represented / files_total), 4) if files_total else 0.0
        )
        self.metrics.memory_bytes = self._estimate_memory()

    def _estimate_memory(self) -> int:
        """Shallow in-process footprint of the adjacency, in bytes.

        Deliberately an estimate over the graph's own containers — the
        intelligence it references is measured separately as `index_size`, and
        counting it here would double-count the very data this layer avoids
        duplicating.
        """
        total = sys.getsizeof(self.nodes) + sys.getsizeof(self.edges)
        total += sum(sys.getsizeof(i) for i in self.nodes)
        total += len(self.edges) * 96  # KGEdge with short strings, measured
        total += sum(sys.getsizeof(v) for v in self._adjacency.out.values())
        total += sum(sys.getsizeof(v) for v in self._adjacency.inc.values())
        return total

    def record_query(self, elapsed_ms: float) -> None:
        self.metrics.query_count += 1
        self.metrics.query_total_ms += elapsed_ms


# ============================================================== adapters


def _adapt_repository_graph(kg: RepositoryKnowledgeGraph) -> None:
    """Structure: files, packages, classes, functions, methods, tests, config.

    Node ids are carried across unchanged from `RepositoryGraph`, which is what
    lets every other adapter attach to them without a translation table.
    """
    source = kg.intelligence.repository_graph

    for node in source.nodes.values():
        node_type: NodeType
        if node.kind in ("function", "method", "class", "file", "test", "package"):
            node_type = node.kind  # type: ignore[assignment]
        elif node.kind == "configuration":
            node_type = "config"
        elif node.kind == "repository":
            node_type = "repository"
        elif node.kind == "directory":
            node_type = "package"
        elif node.kind == "variable":
            continue  # module constants add noise without adding reachability
        else:
            continue

        attributes = dict(node.attributes)
        # Line span is a field on RepoNode; carry it into attributes so queries
        # can order by source position without reaching back into the source.
        attributes["lineno"] = node.lineno
        attributes["end_lineno"] = node.end_lineno

        kg.add_node(
            KGNode(
                id=node.id,
                type=node_type,
                name=node.name,
                file=node.file,
                qualname=str(node.attributes.get("qualname") or node.name),
                attributes=attributes,
            )
        )

    for edge in source.edges:
        if edge.kind in ("contains", "defines", "imports", "inherits", "implements", "references"):
            kg.add_edge(
                edge.source,
                edge.target,
                edge.kind.upper(),
                provenance="repository_graph",
                evidence=f"{edge.kind} relation from repository structure",
            )
        elif edge.kind == "depends_on":
            kg.add_edge(
                edge.source,
                edge.target,
                "DEPENDS_ON",
                provenance="repository_graph",
                evidence=f"depends on external module {edge.attributes.get('module', '')}",
            )
        elif edge.kind == "tests":
            kg.add_edge(
                edge.source,
                edge.target,
                "TESTS",
                provenance="repository_graph",
                evidence="test module imports this file",
            )


def _adapt_api_surface(kg: RepositoryKnowledgeGraph) -> None:
    """Promote route-decorated callables to `api` nodes.

    An API node is not a copy of the function: it is a distinct node marking the
    external surface, linked by EXPOSES. That keeps "which functions exist" and
    "which are externally reachable" as separate, answerable questions.
    """
    for node in list(kg.nodes.values()):
        if node.type not in ("function", "method"):
            continue
        decorators = [str(d).lower() for d in node.attributes.get("decorators", [])]
        matched = sorted({d for d in decorators if d in API_DECORATORS})
        if not matched:
            continue

        api_node = f"api:{node.file}::{node.qualname}"
        kg.add_node(
            KGNode(
                id=api_node,
                type="api",
                name=node.name,
                file=node.file,
                qualname=node.qualname,
                attributes={"decorators": matched},
            )
        )
        kg.add_edge(
            api_node,
            node.id,
            "EXPOSES",
            provenance="repository_graph",
            evidence=f"decorated with @{matched[0]}",
        )


def _adapt_call_graph(kg: RepositoryKnowledgeGraph) -> None:
    """Behaviour: CALLS between callables, plus VALIDATES from tests.

    A call originating in a test file is additionally recorded as VALIDATES,
    which is the edge that answers "what proves this function works?" — the
    question A5.5 needs and no single existing structure could answer.
    """
    call_graph = kg.intelligence.call_graph

    for site in call_graph.call_sites:
        caller_node = call_graph.nodes.get(site.caller)
        callee_node = call_graph.nodes.get(site.callee)
        if caller_node is None or callee_node is None:
            continue

        source = callable_node_id(caller_node.file, caller_node.qualname, caller_node.is_method)
        target = callable_node_id(callee_node.file, callee_node.qualname, callee_node.is_method)

        detail = "attribute dispatch" if site.via_attribute else "direct call"
        if site.via_decorator:
            detail = "decorator application"
        kg.add_edge(
            source,
            target,
            "CALLS",
            provenance="call_graph",
            evidence=f"{detail} in {caller_node.file}",
        )

        if is_test_file(caller_node.file) and not is_test_file(callee_node.file):
            kg.add_edge(
                source,
                target,
                "VALIDATES",
                provenance="call_graph",
                evidence=f"exercised by {caller_node.qualname} in {caller_node.file}",
            )


def _adapt_ownership(kg: RepositoryKnowledgeGraph) -> None:
    """People: owner nodes, OWNS to files, weighted by authorship share."""
    ownership = kg.intelligence.ownership

    for author in sorted(ownership.authors):
        kg.add_node(
            KGNode(
                id=owner_id(author),
                type="owner",
                name=author,
                attributes={"commit_count": ownership.authors[author]},
            )
        )

    for path, entry in sorted(ownership.files.items()):
        file_node = node_id("file", path)
        if file_node not in kg.nodes:
            continue
        for author, share, role in (
            (entry.primary_author, entry.primary_author_share, "primary"),
            (entry.secondary_author, entry.secondary_author_share, "secondary"),
        ):
            if not author:
                continue
            kg.add_node(KGNode(id=owner_id(author), type="owner", name=author))
            kg.add_edge(
                owner_id(author),
                file_node,
                "OWNS",
                weight=share,
                provenance="ownership",
                evidence=(
                    f"{role} author of {path}: {share:.0%} of {entry.commit_count} commit(s), "
                    f"ownership confidence {entry.ownership_confidence:.2f}"
                ),
            )


def _adapt_history(kg: RepositoryKnowledgeGraph) -> None:
    """Time: commits, MODIFIED to files, AUTHORED from owners, CO_CHANGED.

    MODIFIED lands on files rather than functions because that is the resolution
    git history provides here. Attributing a file-level commit to a particular
    function would be a guess, and this layer does not guess.
    """
    history = kg.intelligence.history

    for commit in history.commits:
        node = commit_id(commit.sha)
        kg.add_node(
            KGNode(
                id=node,
                type="commit",
                name=commit.sha[:8],
                attributes={
                    "summary": commit.message_summary,
                    "is_fix": commit.is_fix,
                    "author": commit.author,
                    "committed_at": commit.committed_at.isoformat() if commit.committed_at else "",
                },
            )
        )

        if commit.author:
            kg.add_node(KGNode(id=owner_id(commit.author), type="owner", name=commit.author))
            kg.add_edge(
                owner_id(commit.author),
                node,
                "AUTHORED",
                provenance="history",
                evidence=f"authored {commit.sha[:8]}",
            )

        for path in commit.files:
            file_node = node_id("file", str(path).replace("\\", "/"))
            kg.add_edge(
                node,
                file_node,
                "MODIFIED",
                weight=1.0,
                provenance="history",
                evidence=(
                    f"{'fix ' if commit.is_fix else ''}commit {commit.sha[:8]}: "
                    f"{commit.message_summary}"
                ),
            )

    for left, partners in sorted(history.co_change.items()):
        left_node = node_id("file", left)
        if left_node not in kg.nodes:
            continue
        evolution = history.evolution.get(left)
        total = evolution.commit_count if evolution and evolution.commit_count else 0
        for right, count in sorted(partners.items(), key=lambda kv: (-kv[1], kv[0])):
            ratio = round(min(1.0, count / total), 4) if total else 0.0
            kg.add_edge(
                left_node,
                node_id("file", right),
                "CO_CHANGED",
                weight=ratio,
                provenance="history",
                evidence=f"changed together in {count} commit(s) ({ratio:.0%} of this file's history)",
            )


def _adapt_documentation(kg: RepositoryKnowledgeGraph) -> None:
    """Knowledge: document nodes, DESCRIBES to files and to named functions."""
    documentation = kg.intelligence.documentation

    # Function nodes indexed by bare name, dropping ambiguous names: a document
    # naming `run()` when six modules define one identifies none of them.
    by_name: dict[str, list[str]] = {}
    for node in kg.nodes.values():
        if node.type in ("function", "method"):
            by_name.setdefault(node.name, []).append(node.id)
    unique_by_name = {name: ids[0] for name, ids in by_name.items() if len(ids) == 1}

    for position, entry in enumerate(documentation.entries):
        node = document_id(entry.path, position)
        kg.add_node(
            KGNode(
                id=node,
                type="document",
                name=entry.title or PurePosixPath(entry.path).name,
                file=entry.path,
                attributes={
                    "kind": entry.kind,
                    "topics": entry.topics,
                    "excerpt": entry.excerpt,
                },
            )
        )

        for module in entry.linked_modules:
            kg.add_edge(
                node,
                node_id("file", module),
                "DESCRIBES",
                provenance="documentation",
                evidence=f"{entry.kind} '{entry.title or entry.path}' references {module}",
            )

        for function_name in entry.referenced_functions:
            target = unique_by_name.get(function_name)
            if target:
                kg.add_edge(
                    node,
                    target,
                    "DESCRIBES",
                    provenance="documentation",
                    evidence=f"{entry.path} documents {function_name}()",
                )


def _adapt_repair_memory(kg: RepositoryKnowledgeGraph) -> None:
    """Experience: repair nodes, FIXED to code, AFFECTS to collateral files."""
    memory = kg.intelligence.repair_memory

    for record in memory.records:
        node = repair_id(record.repair_id)
        kg.add_node(
            KGNode(
                id=node,
                type="repair",
                name=record.repair_id,
                file=record.file,
                attributes={
                    "bug_type": record.bug_type,
                    "validation_passed": record.validation_passed,
                    "mutation_score": record.mutation_score,
                    "security_score": record.security_score,
                    "retry_count": record.retry_count,
                    "pr_type": record.pr_type,
                    "recorded_at": record.recorded_at.isoformat(),
                },
            )
        )

        outcome = "validated" if record.validation_passed else "unvalidated"
        target = None
        if record.function:
            for kind in ("function", "method"):
                candidate = node_id(kind, record.file, record.function)
                if candidate in kg.nodes:
                    target = candidate
                    break
        if target is None:
            target = node_id("file", record.file)

        kg.add_edge(
            node,
            target,
            "FIXED",
            weight=1.0 if record.validation_passed else 0.5,
            provenance="repair_memory",
            evidence=f"{outcome} {record.bug_type} repair after {record.retry_count} retry(ies)",
        )

        for affected in record.affected_files:
            if affected != record.file:
                kg.add_edge(
                    node,
                    node_id("file", affected),
                    "AFFECTS",
                    provenance="repair_memory",
                    evidence=f"changed alongside {record.file} in the same repair",
                )


ADAPTERS = (
    _adapt_repository_graph,
    _adapt_api_surface,
    _adapt_call_graph,
    _adapt_ownership,
    _adapt_history,
    _adapt_documentation,
    _adapt_repair_memory,
)


def build_knowledge_graph(intelligence: RepositoryIntelligence) -> RepositoryKnowledgeGraph:
    """Project the six analyses into one connected graph. Deterministic."""
    started = time.monotonic()
    kg = RepositoryKnowledgeGraph(intelligence)
    for adapter in ADAPTERS:
        adapter(kg)
    kg.finalize_metrics(int((time.monotonic() - started) * 1000))
    return kg


# ============================================================ query engine


def _timed(method):
    """Record query latency on the graph's metrics."""

    def wrapper(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return method(self, *args, **kwargs)
        finally:
            self.graph.record_query((time.perf_counter() - started) * 1000)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class KnowledgeQueryEngine:
    """Deterministic traversal queries. No LLM, no ranking model, no randomness.

    Every method returns a totally ordered result, so repeated calls against the
    same graph produce byte-identical answers.
    """

    def __init__(self, graph: RepositoryKnowledgeGraph):
        self.graph = graph

    # -- structure -------------------------------------------------------

    @_timed
    def functions_in_file(self, file: str) -> list[KGNode]:
        """Callables defined in a file, in source order."""
        nodes = [n for n in self.graph.nodes_in_file(file) if n.type in ("function", "method")]
        return sorted(nodes, key=lambda n: (n.attributes.get("lineno", 0), n.qualname))

    @_timed
    def classes_in_file(self, file: str) -> list[KGNode]:
        return sorted(
            (n for n in self.graph.nodes_in_file(file) if n.type == "class"),
            key=lambda n: n.name,
        )

    @_timed
    def api_surface(self) -> list[KGNode]:
        """Externally reachable callables, by file then name."""
        return sorted(self.graph.nodes_of_type("api"), key=lambda n: (n.file, n.qualname))

    # -- behaviour -------------------------------------------------------

    @_timed
    def functions_called_by(self, file: str, qualname: str | None = None, hops: int = 1) -> list[KGNode]:
        """Callees reachable from a function, or from every function in a file."""
        starts = self._callable_starts(file, qualname)
        reached: dict[str, int] = {}
        for start in starts:
            for node_ref, distance in self.graph.traverse(start, ("CALLS",), hops).items():
                if node_ref not in starts and (node_ref not in reached or distance < reached[node_ref]):
                    reached[node_ref] = distance
        return self._ordered(reached)

    @_timed
    def callers_of(self, file: str, qualname: str | None = None, hops: int = 1) -> list[KGNode]:
        starts = self._callable_starts(file, qualname)
        reached: dict[str, int] = {}
        for start in starts:
            for node_ref, distance in self.graph.traverse(start, ("CALLS",), hops, "in").items():
                if node_ref not in starts and (node_ref not in reached or distance < reached[node_ref]):
                    reached[node_ref] = distance
        return self._ordered(reached)

    @_timed
    def call_chain(self, file: str, qualname: str, hops: int = 2) -> list[tuple[KGNode, int]]:
        """Both directions with hop distance — the shape A5.5 shows a patch model."""
        start = self._resolve_callable(file, qualname)
        if start is None:
            return []
        reached = self.graph.traverse(start, ("CALLS",), hops, "both")
        reached.pop(start, None)
        return [
            (self.graph.nodes[i], d)
            for i, d in sorted(reached.items(), key=lambda kv: (kv[1], kv[0]))
        ]

    @_timed
    def related_functions(self, file: str, qualname: str, hops: int = 2) -> list[KGNode]:
        """Functions related by calls, containment, or shared repair history."""
        start = self._resolve_callable(file, qualname)
        if start is None:
            return []
        reached = self.graph.traverse(start, ("CALLS", "CONTAINS", "REFERENCES"), hops, "both")
        reached.pop(start, None)
        return [
            self.graph.nodes[i]
            for i, _d in sorted(reached.items(), key=lambda kv: (kv[1], kv[0]))
            if self.graph.nodes[i].type in ("function", "method")
        ]

    @_timed
    def supporting_tests(self, file: str, qualname: str | None = None) -> list[KGNode]:
        """Test callables that exercise this code, via VALIDATES."""
        results: dict[str, KGNode] = {}
        for start in self._callable_starts(file, qualname):
            for edge in self.graph.in_edges(start, "VALIDATES"):
                results[edge.source] = self.graph.nodes[edge.source]

        if not results:
            # Fall back to file-level TESTS edges when no call resolved.
            for edge in self.graph.in_edges(node_id("file", file), "TESTS"):
                results[edge.source] = self.graph.nodes[edge.source]

        return sorted(results.values(), key=lambda n: (n.file, n.qualname))

    # -- people ----------------------------------------------------------

    @_timed
    def files_owned_by(self, author: str) -> list[tuple[KGNode, float]]:
        """Files an author owns, strongest share first."""
        edges = self.graph.out_edges(owner_id(author), "OWNS")
        pairs = [
            (self.graph.nodes[e.target], e.weight)
            for e in edges
            if e.target in self.graph.nodes
        ]
        return sorted(pairs, key=lambda pair: (-pair[1], pair[0].file))

    @_timed
    def owners_of(self, file: str) -> list[tuple[KGNode, float]]:
        edges = self.graph.in_edges(node_id("file", file), "OWNS")
        pairs = [(self.graph.nodes[e.source], e.weight) for e in edges]
        return sorted(pairs, key=lambda pair: (-pair[1], pair[0].name))

    @_timed
    def unowned_files(self) -> list[KGNode]:
        """Files with no OWNS edge — no git history attributes them to anyone."""
        return sorted(
            (
                n
                for n in self.graph.file_nodes()
                if not self.graph.in_edges(n.id, "OWNS")
            ),
            key=lambda n: n.file,
        )

    # -- history ---------------------------------------------------------

    @_timed
    def commits_touching(self, file: str, limit: int = 10) -> list[KGNode]:
        edges = self.graph.in_edges(node_id("file", file), "MODIFIED")
        commits = [self.graph.nodes[e.source] for e in edges]
        commits.sort(key=lambda n: (n.attributes.get("committed_at", ""), n.name), reverse=True)
        return commits[:limit]

    @_timed
    def co_changed_files(self, file: str, limit: int = 5) -> list[tuple[KGNode, float]]:
        """Files that historically change with this one, strongest coupling first."""
        edges = self.graph.out_edges(node_id("file", file), "CO_CHANGED")
        pairs = [
            (self.graph.nodes[e.target], e.weight)
            for e in edges
            if e.target in self.graph.nodes
        ]
        pairs.sort(key=lambda pair: (-pair[1], pair[0].file))
        return pairs[:limit]

    @_timed
    def historical_bug_hotspots(self, limit: int = 10) -> list[tuple[KGNode, float]]:
        """Files ranked by bug-fix churn from the indexed history."""
        history = self.graph.intelligence.history
        scored: list[tuple[KGNode, float]] = []
        for path, evolution in history.evolution.items():
            node = self.graph.node(node_id("file", path))
            if node is not None and evolution.fix_commit_count > 0:
                scored.append((node, evolution.churn))
        scored.sort(key=lambda pair: (-pair[1], pair[0].file))
        return scored[:limit]

    @_timed
    def recent_high_churn_modules(self, limit: int = 10, min_churn: float = 0.3) -> list[tuple[KGNode, float]]:
        """Hot files: high bug-fix churn *and* touched inside the recent window."""
        history = self.graph.intelligence.history
        ownership = self.graph.intelligence.ownership
        scored: list[tuple[KGNode, float]] = []

        for path, evolution in history.evolution.items():
            if evolution.churn < min_churn:
                continue
            entry = ownership.files.get(path)
            if entry is None or not entry.recent_modifications:
                continue
            node = self.graph.node(node_id("file", path))
            if node is not None:
                scored.append((node, evolution.churn))

        scored.sort(key=lambda pair: (-pair[1], pair[0].file))
        return scored[:limit]

    # -- knowledge -------------------------------------------------------

    @_timed
    def documentation_for(self, file: str, qualname: str | None = None) -> list[KGNode]:
        """Documents describing a file, or a specific function within it."""
        targets = [node_id("file", file)]
        if qualname:
            resolved = self._resolve_callable(file, qualname)
            if resolved:
                targets.append(resolved)

        results: dict[str, KGNode] = {}
        for target in targets:
            for edge in self.graph.in_edges(target, "DESCRIBES"):
                results[edge.source] = self.graph.nodes[edge.source]
        return sorted(results.values(), key=lambda n: (n.file, n.name))

    @_timed
    def validated_repairs(self, file: str | None = None, limit: int = 10) -> list[KGNode]:
        """Repairs that passed validation, most recent first."""
        repairs = [
            n
            for n in self.graph.nodes_of_type("repair")
            if n.attributes.get("validation_passed")
            and (file is None or n.file == file)
        ]
        repairs.sort(key=lambda n: (n.attributes.get("recorded_at", ""), n.name), reverse=True)
        return repairs[:limit]

    @_timed
    def repairs_touching(self, file: str) -> list[KGNode]:
        """Repairs that fixed or collaterally affected a file."""
        file_node = node_id("file", file)
        results: dict[str, KGNode] = {}
        for edge_type in ("FIXED", "AFFECTS"):
            for edge in self.graph.in_edges(file_node, edge_type):
                results[edge.source] = self.graph.nodes[edge.source]
        for node in self.graph.nodes_in_file(file):
            for edge in self.graph.in_edges(node.id, "FIXED"):
                results[edge.source] = self.graph.nodes[edge.source]
        return sorted(results.values(), key=lambda n: n.name)

    # -- helpers ---------------------------------------------------------

    def _resolve_callable(self, file: str, qualname: str) -> str | None:
        for kind in ("function", "method"):
            candidate = node_id(kind, file, qualname)
            if candidate in self.graph.nodes:
                return candidate
        # Bare name given for a method: match on the trailing segment.
        for node in self.graph.nodes_in_file(file):
            if node.type in ("function", "method") and node.name == qualname:
                return node.id
        return None

    def _callable_starts(self, file: str, qualname: str | None) -> set[str]:
        if qualname:
            resolved = self._resolve_callable(file, qualname)
            return {resolved} if resolved else set()
        return {n.id for n in self.graph.nodes_in_file(file) if n.type in ("function", "method")}

    def _ordered(self, reached: dict[str, int]) -> list[KGNode]:
        return [
            self.graph.nodes[i]
            for i, _d in sorted(reached.items(), key=lambda kv: (kv[1], kv[0]))
            if i in self.graph.nodes
        ]
