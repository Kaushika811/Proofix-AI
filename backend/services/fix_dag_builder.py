"""Build fix DAG with real dependency edges from SIG imports and CVE reachability."""

from __future__ import annotations

import networkx as nx

from backend.models.cve import CVERecord
from backend.models.fix_dag import DependencyEdge, FixNode
from backend.models.sig import SemanticIntentGraph


def build_cve_reach_paths(sig: SemanticIntentGraph | None, package: str) -> list[str]:
    if sig is None:
        return []
    pkg = package.lower().replace("-", "_")
    paths: list[str] = []
    for path, node in sig.files.items():
        for imp in node.imports:
            if imp.lower().replace("-", "_") == pkg:
                paths.append(path)
    return paths


def build_fix_nodes(
    findings: list[dict],
    cve_records: list[CVERecord],
    scope_files: list[str],
) -> list[FixNode]:
    nodes: list[FixNode] = []
    seen_ids: set[str] = set()

    for finding in findings[:8]:
        issue_id = finding.get("id") or f"finding-{finding.get('file', 'unknown')}"
        if issue_id in seen_ids:
            continue
        seen_ids.add(issue_id)
        nodes.append(FixNode(issue_id=issue_id, files=[finding["file"]], depends_on=[]))

    for record in cve_records:
        if record.classification != "Critical":
            continue
        issue_id = f"cve-{record.cve_id}"
        if issue_id in seen_ids:
            continue
        seen_ids.add(issue_id)
        nodes.append(FixNode(issue_id=issue_id, files=list(scope_files[:2]), depends_on=[]))

    if not nodes and scope_files:
        nodes.append(FixNode(issue_id="fix-0", files=list(scope_files), depends_on=[]))

    return nodes


def build_dependency_edges(
    nodes: list[FixNode],
    sig: SemanticIntentGraph | None,
    cve_records: list[CVERecord],
) -> list[DependencyEdge]:
    edges: list[DependencyEdge] = []
    issue_by_file: dict[str, list[str]] = {}
    for node in nodes:
        for file_path in node.files:
            issue_by_file.setdefault(file_path, []).append(node.issue_id)

    if sig:
        for node in nodes:
            for file_path in node.files:
                file_node = sig.files.get(file_path)
                if not file_node:
                    continue
                for imp in file_node.imports:
                    for target_path in _resolve_import_to_files(sig, imp):
                        for upstream_issue in issue_by_file.get(target_path, []):
                            if upstream_issue == node.issue_id:
                                continue
                            edges.append(
                                DependencyEdge(
                                    from_issue=upstream_issue,
                                    to_issue=node.issue_id,
                                    reason=f"import_graph:{target_path}->{file_path}",
                                )
                            )

    for record in cve_records:
        if record.classification != "Critical":
            continue
        cve_issue = f"cve-{record.cve_id}"
        reach_files = build_cve_reach_paths(sig, record.package)
        for path in reach_files:
            for app_issue in issue_by_file.get(path, []):
                if app_issue == cve_issue:
                    continue
                edges.append(
                    DependencyEdge(
                        from_issue=cve_issue,
                        to_issue=app_issue,
                        reason=f"cve_reachability:{record.package}->{path}",
                    )
                )

    return _dedupe_edges(edges)


def apply_dependencies(nodes: list[FixNode], edges: list[DependencyEdge]) -> list[FixNode]:
    deps_by_issue: dict[str, set[str]] = {n.issue_id: set() for n in nodes}
    for edge in edges:
        deps_by_issue.setdefault(edge.to_issue, set()).add(edge.from_issue)

    updated: list[FixNode] = []
    for node in nodes:
        updated.append(
            FixNode(
                issue_id=node.issue_id,
                files=node.files,
                depends_on=sorted(deps_by_issue.get(node.issue_id, set())),
            )
        )
    return updated


def topological_execution_order(nodes: list[FixNode], edges: list[DependencyEdge]) -> list[str]:
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node.issue_id)
    for edge in edges:
        graph.add_edge(edge.from_issue, edge.to_issue)

    try:
        return list(nx.topological_sort(graph))
    except nx.NetworkXUnfeasible:
        # Import-derived dependencies can legitimately cycle. A6 still needs a
        # stable execution order for the rest of the pipeline, so degrade to
        # the documented CVE-first fallback instead of failing the whole run.
        cve_first = [n.issue_id for n in nodes if n.issue_id.startswith("cve-")]
        rest = [n.issue_id for n in nodes if not n.issue_id.startswith("cve-")]
        return cve_first + rest


def detect_conflict_batches(nodes: list[FixNode]) -> list[list[str]]:
    file_to_issues: dict[str, list[str]] = {}
    for node in nodes:
        for file_path in node.files:
            file_to_issues.setdefault(file_path, []).append(node.issue_id)
    return [sorted(set(issues)) for issues in file_to_issues.values() if len(set(issues)) > 1]


def _resolve_import_to_files(sig: SemanticIntentGraph, module: str) -> list[str]:
    module_lower = module.lower().replace("-", "_")
    matches: list[str] = []
    for path in sig.files:
        stem = path.replace("/", ".").replace(".py", "").split(".")[-1]
        if stem.lower() == module_lower:
            matches.append(path)
            continue
        if path.endswith(f"{module_lower}.py") or f"/{module_lower}.py" in path:
            matches.append(path)
    return matches


def _dedupe_edges(edges: list[DependencyEdge]) -> list[DependencyEdge]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[DependencyEdge] = []
    for edge in edges:
        key = (edge.from_issue, edge.to_issue, edge.reason)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique
