"""Export the knowledge graph in the formats a frontend or tool can render.

Four formats, one filtering model. `GraphView` selects a subgraph — by node
type, edge type, file scope, or a neighbourhood around a focus node — and every
exporter renders whatever the view produced. That keeps the six requested maps
(repository, dependency, repair history, ownership, hotspots, architecture) as
*views* rather than six bespoke exporters.

A full repository graph is far too large to render: this codebase alone produces
~1,900 nodes and ~8,600 edges, which no force-directed layout will make legible.
Every view therefore has a node cap, applied by degree so the most connected —
and most explanatory — nodes survive truncation. `GraphView.truncated` reports
when this happened, so a frontend can say so rather than silently showing part
of a graph as if it were the whole.

Escaping is handled per format. Untrusted repository content (file paths, commit
messages, docstrings) reaches these strings, so XML, DOT and Mermaid each get
their own escaping rather than a shared approximation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as xml_escape, quoteattr

from backend.models.knowledge_graph import KGEdge, KGNode
from backend.services.knowledge_graph import RepositoryKnowledgeGraph

# Rendering ceiling. Above this a layout engine produces an unreadable hairball.
DEFAULT_MAX_NODES = 300

# Stable per-type colours, so the same type reads the same way across views.
TYPE_COLORS = {
    "repository": "#4C566A",
    "package": "#5E81AC",
    "file": "#81A1C1",
    "class": "#8FBCBB",
    "function": "#A3BE8C",
    "method": "#B8CC96",
    "api": "#EBCB8B",
    "test": "#88C0D0",
    "config": "#D08770",
    "owner": "#B48EAD",
    "commit": "#9A8C98",
    "document": "#D8DEE9",
    "repair": "#BF616A",
    "capability": "#E5A50A",
}

TYPE_SHAPES = {
    "repository": "house",
    "package": "folder",
    "file": "box",
    "class": "component",
    "function": "ellipse",
    "method": "ellipse",
    "api": "doubleoctagon",
    "test": "box",
    "config": "note",
    "owner": "circle",
    "commit": "diamond",
    "document": "note",
    "repair": "octagon",
    "capability": "hexagon",
}

_MERMAID_ID = re.compile(r"[^A-Za-z0-9_]")


@dataclass
class GraphView:
    """A selected, capped subgraph ready for rendering."""

    nodes: list[KGNode] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)
    name: str = "repository"
    truncated: bool = False
    total_nodes: int = 0

    @property
    def node_ids(self) -> set[str]:
        return {n.id for n in self.nodes}


def build_view(
    graph: RepositoryKnowledgeGraph,
    *,
    name: str = "repository",
    node_types: tuple[str, ...] | None = None,
    edge_types: tuple[str, ...] | None = None,
    files: tuple[str, ...] | None = None,
    focus: str | None = None,
    hops: int = 2,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> GraphView:
    """Select a subgraph. Deterministic: ties break on node id."""
    if focus:
        selected_ids = set(graph.traverse(focus, edge_types, hops, "both"))
    else:
        selected_ids = set(graph.nodes)

    if node_types:
        selected_ids = {i for i in selected_ids if graph.nodes[i].type in node_types}
    if files:
        allowed = set(files)
        selected_ids = {
            i for i in selected_ids
            if graph.nodes[i].file in allowed or graph.nodes[i].type in ("owner", "capability")
        }

    total = len(selected_ids)
    truncated = False
    if len(selected_ids) > max_nodes:
        truncated = True
        ranked = sorted(selected_ids, key=lambda i: (-graph.degree(i), i))
        selected_ids = set(ranked[:max_nodes])

    nodes = sorted((graph.nodes[i] for i in selected_ids), key=lambda n: (n.type, n.id))
    edges = [
        e
        for e in graph.edges
        if e.source in selected_ids
        and e.target in selected_ids
        and (edge_types is None or e.type in edge_types)
    ]

    return GraphView(
        nodes=nodes,
        edges=edges,
        name=name,
        truncated=truncated,
        total_nodes=total,
    )


# ------------------------------------------------------------- named views


def repository_map(graph: RepositoryKnowledgeGraph, **kwargs) -> GraphView:
    """Packages and files — the structural overview."""
    return build_view(
        graph,
        name="repository_map",
        node_types=("repository", "package", "file", "test", "config"),
        edge_types=("CONTAINS",),
        **kwargs,
    )


def dependency_map(graph: RepositoryKnowledgeGraph, **kwargs) -> GraphView:
    """Who imports and depends on whom."""
    return build_view(
        graph,
        name="dependency_map",
        node_types=("file", "test", "package"),
        edge_types=("IMPORTS", "DEPENDS_ON"),
        **kwargs,
    )


def call_map(graph: RepositoryKnowledgeGraph, **kwargs) -> GraphView:
    """Callables and the calls between them."""
    return build_view(
        graph,
        name="call_map",
        node_types=("function", "method", "api"),
        edge_types=("CALLS", "EXPOSES"),
        **kwargs,
    )


def ownership_map(graph: RepositoryKnowledgeGraph, **kwargs) -> GraphView:
    """Authors and the files they own."""
    return build_view(
        graph,
        name="ownership_map",
        node_types=("owner", "file", "test"),
        edge_types=("OWNS",),
        **kwargs,
    )


def repair_history_map(graph: RepositoryKnowledgeGraph, **kwargs) -> GraphView:
    """Recorded repairs and the code they touched."""
    return build_view(
        graph,
        name="repair_history_map",
        node_types=("repair", "file", "function", "method"),
        edge_types=("FIXED", "AFFECTS"),
        **kwargs,
    )


def architecture_map(graph: RepositoryKnowledgeGraph, **kwargs) -> GraphView:
    """Capabilities and the files that compose them."""
    return build_view(
        graph,
        name="architecture_map",
        node_types=("capability", "file", "api"),
        edge_types=("PART_OF", "EXPOSES"),
        **kwargs,
    )


def hotspot_map(
    graph: RepositoryKnowledgeGraph,
    hotspots,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> GraphView:
    """Only the files named by architectural findings, plus what links them."""
    from backend.services.repository_graph import node_id

    targets = {h.target.split("::", 1)[0] for h in hotspots}
    targets |= {m.split("::", 1)[0] for h in hotspots for m in h.members}
    ids = {node_id("file", t) for t in targets} & set(graph.nodes)

    view = build_view(
        graph,
        name="hotspot_map",
        node_types=("file", "test"),
        edge_types=("IMPORTS", "CO_CHANGED"),
        max_nodes=max_nodes,
    )
    view.nodes = [n for n in view.nodes if n.id in ids]
    keep = {n.id for n in view.nodes}
    view.edges = [e for e in view.edges if e.source in keep and e.target in keep]
    view.total_nodes = len(ids)
    return view


NAMED_VIEWS = {
    "repository": repository_map,
    "dependency": dependency_map,
    "call": call_map,
    "ownership": ownership_map,
    "repair": repair_history_map,
    "architecture": architecture_map,
}


# --------------------------------------------------------------- exporters


def to_json(view: GraphView) -> str:
    """Node-link JSON — the format a JS graph library consumes directly."""
    return json.dumps(
        {
            "name": view.name,
            "truncated": view.truncated,
            "total_nodes": view.total_nodes,
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "label": n.name,
                    "file": n.file,
                    "qualname": n.qualname,
                    "color": TYPE_COLORS.get(n.type, "#CCCCCC"),
                    "attributes": n.attributes,
                }
                for n in view.nodes
            ],
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "type": e.type,
                    "weight": e.weight,
                    "provenance": e.provenance,
                    "evidence": e.evidence,
                }
                for e in view.edges
            ],
        },
        indent=2,
        sort_keys=False,
        default=str,
    )


def to_graphml(view: GraphView) -> str:
    """GraphML — the interchange format Gephi, yEd and networkx all read."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="type" for="node" attr.name="type" attr.type="string"/>',
        '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="file" for="node" attr.name="file" attr.type="string"/>',
        '  <key id="color" for="node" attr.name="color" attr.type="string"/>',
        '  <key id="etype" for="edge" attr.name="type" attr.type="string"/>',
        '  <key id="weight" for="edge" attr.name="weight" attr.type="double"/>',
        '  <key id="provenance" for="edge" attr.name="provenance" attr.type="string"/>',
        '  <key id="evidence" for="edge" attr.name="evidence" attr.type="string"/>',
        f'  <graph id={quoteattr(view.name)} edgedefault="directed">',
    ]

    for node in view.nodes:
        lines.append(f"    <node id={quoteattr(node.id)}>")
        lines.append(f'      <data key="type">{xml_escape(node.type)}</data>')
        lines.append(f'      <data key="label">{xml_escape(node.name)}</data>')
        lines.append(f'      <data key="file">{xml_escape(node.file)}</data>')
        lines.append(f'      <data key="color">{TYPE_COLORS.get(node.type, "#CCCCCC")}</data>')
        lines.append("    </node>")

    for position, edge in enumerate(view.edges):
        lines.append(
            f'    <edge id="e{position}" source={quoteattr(edge.source)} target={quoteattr(edge.target)}>'
        )
        lines.append(f'      <data key="etype">{xml_escape(edge.type)}</data>')
        lines.append(f'      <data key="weight">{edge.weight}</data>')
        lines.append(f'      <data key="provenance">{xml_escape(edge.provenance)}</data>')
        lines.append(f'      <data key="evidence">{xml_escape(edge.evidence)}</data>')
        lines.append("    </edge>")

    lines.append("  </graph>")
    lines.append("</graphml>")
    return "\n".join(lines)


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def to_dot(view: GraphView) -> str:
    """Graphviz DOT — for static rendering in CI or documentation."""
    lines = [
        f'digraph "{_dot_escape(view.name)}" {{',
        "  rankdir=LR;",
        '  node [style=filled, fontname="Helvetica", fontsize=10];',
        '  edge [fontname="Helvetica", fontsize=8];',
    ]

    for node in view.nodes:
        lines.append(
            f'  "{_dot_escape(node.id)}" ['
            f'label="{_dot_escape(node.name)}", '
            f'shape={TYPE_SHAPES.get(node.type, "box")}, '
            f'fillcolor="{TYPE_COLORS.get(node.type, "#CCCCCC")}"];'
        )

    for edge in view.edges:
        lines.append(
            f'  "{_dot_escape(edge.source)}" -> "{_dot_escape(edge.target)}" '
            f'[label="{_dot_escape(edge.type)}"];'
        )

    lines.append("}")
    return "\n".join(lines)


def to_mermaid(view: GraphView, max_nodes: int = 80) -> str:
    """Mermaid flowchart — renders inline in the frontend without a layout lib.

    Capped harder than the other formats: Mermaid is for embedding in a page,
    and beyond a few dozen nodes it stops being readable at all.
    """
    nodes = view.nodes[:max_nodes]
    keep = {n.id for n in nodes}
    edges = [e for e in view.edges if e.source in keep and e.target in keep]

    aliases: dict[str, str] = {}
    for position, node in enumerate(nodes):
        aliases[node.id] = f"n{position}"

    lines = ["flowchart LR"]
    for node in nodes:
        label = node.name.replace('"', "'").replace("\n", " ")[:40]
        alias = aliases[node.id]
        # Shape by type, so the diagram reads without a legend.
        if node.type in ("function", "method"):
            lines.append(f'  {alias}(["{label}"])')
        elif node.type in ("api", "capability"):
            lines.append(f'  {alias}{{{{"{label}"}}}}')
        elif node.type in ("owner", "commit"):
            lines.append(f'  {alias}[/"{label}"/]')
        else:
            lines.append(f'  {alias}["{label}"]')

    for edge in edges:
        label = _MERMAID_ID.sub("_", edge.type)
        lines.append(f"  {aliases[edge.source]} -->|{label}| {aliases[edge.target]}")

    for node_type in sorted({n.type for n in nodes}):
        color = TYPE_COLORS.get(node_type, "#CCCCCC")
        members = [aliases[n.id] for n in nodes if n.type == node_type]
        if members:
            lines.append(f"  classDef {node_type} fill:{color},stroke:#333,color:#111;")
            lines.append(f"  class {','.join(members)} {node_type};")

    if view.truncated or len(view.nodes) > max_nodes:
        lines.append(f"  %% truncated: showing {len(nodes)} of {view.total_nodes} nodes")

    return "\n".join(lines)


EXPORTERS = {
    "json": to_json,
    "graphml": to_graphml,
    "dot": to_dot,
    "mermaid": to_mermaid,
}


def export(view: GraphView, fmt: str) -> str:
    """Render a view in one of: json, graphml, dot, mermaid."""
    exporter = EXPORTERS.get(fmt.lower())
    if exporter is None:
        raise ValueError(f"unknown export format {fmt!r}; expected one of {sorted(EXPORTERS)}")
    return exporter(view)
