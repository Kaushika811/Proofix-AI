"""Deterministic risk scoring over the knowledge graph.

Nine signals, each with a fixed weight declared at module level, each producing
an `Evidence` record naming its value, its contribution, and the graph edges or
source structure that justified it. The score is the sum; there is no model, no
threshold learned from data, and no signal that cannot be printed.

Two of the nine deserve their reasoning stated, because they are the ones most
easily got backwards:

**Ownership is inverted.** Concentrated, recent ownership *reduces* risk. A file
with a clear maintainer who touched it last month is understood; a file whose
authorship is split six ways, or which nobody has touched in a year, is where a
change is most likely to surprise someone. Low ownership confidence is therefore
the risk signal, not high.

**Documentation is inverted for the same reason.** Absence of documentation
contributes risk; presence contributes nothing. This layer never rewards a file
for being documented, because that would let a well-commented but heavily
churning module outrank a quiet one.

The output is per *module* (file). Function-level risk is deliberately not
produced: the underlying git history is file-resolution, so a function-level
score would carry a precision the evidence does not support.
"""

from __future__ import annotations


from backend.models.knowledge_graph import (
    Evidence,
    Explanation,
    RiskAssessment,
    RiskBand,
)
from backend.services.knowledge_graph import RepositoryKnowledgeGraph
from backend.services.repository_graph import is_test_file, node_id

# -- signal weights --------------------------------------------------------
# Ordered by how directly the signal predicts that a change here goes wrong.
# History of actual defects outranks structural shape, which outranks absence
# of supporting material.

W_BUG_HISTORY = 0.22        # this file has needed bug-fix commits
W_CHURN = 0.16              # bug-fix commit density
W_REPAIR_HISTORY = 0.14     # the pipeline itself has repaired this file
W_MUTATION_FAILURE = 0.12   # a repair here left a surviving mutant
W_COMPLEXITY = 0.12         # peak cyclomatic complexity in the file
W_FAN_IN = 0.10             # how much depends on it — blast radius
W_LOW_OWNERSHIP = 0.08      # diffuse or stale ownership
W_FAN_OUT = 0.03            # how much it depends on — coupling
W_NO_DOCUMENTATION = 0.03   # nothing in the repository explains it

# Saturation points. Beyond these, more of the same signal says nothing new.
COMPLEXITY_SATURATION = 20.0
FAN_IN_SATURATION = 25.0
FAN_OUT_SATURATION = 20.0
BUG_COMMIT_SATURATION = 5.0
REPAIR_SATURATION = 3.0

# Score bands. Reported alongside the number so a caller never has to guess
# what 0.41 means.
BAND_THRESHOLDS: tuple[tuple[float, RiskBand], ...] = (
    (0.60, "high"),
    (0.40, "elevated"),
    (0.20, "moderate"),
    (0.00, "low"),
)


def risk_band(score: float) -> RiskBand:
    for threshold, band in BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return "low"


def _saturate(value: float, ceiling: float) -> float:
    return min(1.0, value / ceiling) if ceiling > 0 else 0.0


def assess_file(graph: RepositoryKnowledgeGraph, file: str) -> RiskAssessment:
    """Score one module. Every contribution is itemised in the explanation."""
    intelligence = graph.intelligence
    file_node = node_id("file", file)
    evidence: list[Evidence] = []

    def add(
        signal: str,
        value: float,
        weight: float,
        detail: str,
        provenance: str,
        edges: list[str] | None = None,
    ) -> None:
        contribution = round(weight * value, 4)
        if contribution <= 0:
            return
        evidence.append(
            Evidence(
                signal=signal,
                value=round(value, 4),
                contribution=contribution,
                detail=detail,
                provenance=provenance,  # type: ignore[arg-type]
                edges=edges or [],
            )
        )

    # -- history ---------------------------------------------------------
    evolution = intelligence.history.evolution.get(file)
    if evolution and evolution.fix_commit_count:
        fix_commits = [
            f"{e.source}->{e.target}"
            for e in graph.in_edges(file_node, "MODIFIED")
            if graph.nodes[e.source].attributes.get("is_fix")
        ]
        add(
            "bug_history",
            _saturate(evolution.fix_commit_count, BUG_COMMIT_SATURATION),
            W_BUG_HISTORY,
            f"{evolution.fix_commit_count} bug-fix commit(s) of {evolution.commit_count} total",
            "history",
            fix_commits[:5],
        )

    if evolution and evolution.churn:
        add(
            "churn",
            evolution.churn,
            W_CHURN,
            f"bug-fix churn {evolution.churn:.2f} relative to the busiest file",
            "history",
        )

    # -- repair experience ------------------------------------------------
    repairs = [r for r in intelligence.repair_memory.records if r.file == file]
    if repairs:
        add(
            "repair_history",
            _saturate(len(repairs), REPAIR_SATURATION),
            W_REPAIR_HISTORY,
            f"{len(repairs)} prior repair(s) recorded by this pipeline",
            "repair_memory",
            [f"repair:{r.repair_id}->{file_node}" for r in repairs[:5]],
        )

    surviving = [
        r for r in repairs
        if r.mutation_score is not None and r.mutation_score < 1.0
    ]
    if surviving:
        add(
            "mutation_failure",
            _saturate(len(surviving), REPAIR_SATURATION),
            W_MUTATION_FAILURE,
            (
                f"{len(surviving)} repair(s) here scored below a full mutation kill — "
                "tests passed without fully exercising the change"
            ),
            "repair_memory",
        )

    # -- structure --------------------------------------------------------
    parsed = intelligence.parsed_modules.get(file)
    if parsed and parsed.function_spans:
        peak = max(span.complexity for span in parsed.function_spans)
        worst = max(parsed.function_spans, key=lambda s: s.complexity)
        if peak > 1:
            add(
                "complexity",
                _saturate(peak, COMPLEXITY_SATURATION),
                W_COMPLEXITY,
                f"peak cyclomatic complexity {peak} in {worst.qualname}()",
                "repository_graph",
            )

    fan_in, fan_out = _fan_metrics(graph, file)
    if fan_in:
        add(
            "fan_in",
            _saturate(fan_in, FAN_IN_SATURATION),
            W_FAN_IN,
            f"{fan_in} inbound call(s) — a defect here propagates widely",
            "call_graph",
        )
    if fan_out:
        add(
            "fan_out",
            _saturate(fan_out, FAN_OUT_SATURATION),
            W_FAN_OUT,
            f"{fan_out} outbound call(s) — depends on much that can change",
            "call_graph",
        )

    # -- inverted signals -------------------------------------------------
    ownership = intelligence.ownership.files.get(file)
    if ownership is None:
        add(
            "low_ownership",
            1.0,
            W_LOW_OWNERSHIP,
            "no git history attributes this file to any author",
            "ownership",
        )
    elif ownership.ownership_confidence < 1.0:
        add(
            "low_ownership",
            round(1.0 - ownership.ownership_confidence, 4),
            W_LOW_OWNERSHIP,
            (
                f"ownership confidence {ownership.ownership_confidence:.2f} "
                f"across {len(ownership.author_counts)} author(s)"
            ),
            "ownership",
            [f"{e.source}->{file_node}" for e in graph.in_edges(file_node, "OWNS")],
        )

    documents = graph.in_edges(file_node, "DESCRIBES")
    if not documents:
        add(
            "no_documentation",
            1.0,
            W_NO_DOCUMENTATION,
            "no README, doc page or module docstring describes this file",
            "documentation",
        )

    score = round(min(1.0, sum(e.contribution for e in evidence)), 4)
    band = risk_band(score)
    top = max(evidence, key=lambda e: e.contribution).detail if evidence else "no risk signals"

    return RiskAssessment(
        module=file,
        risk=score,
        band=band,
        reason=top,
        explanation=Explanation(
            summary=f"{file} scored {score:.2f} ({band}) from {len(evidence)} signal(s)",
            evidence=sorted(evidence, key=lambda e: (-e.contribution, e.signal)),
        ),
    )


def _fan_metrics(graph: RepositoryKnowledgeGraph, file: str) -> tuple[int, int]:
    """Inbound and outbound call counts for a file, from the call graph."""
    fan_in = 0
    fan_out = 0
    for node in graph.nodes_in_file(file):
        if node.type not in ("function", "method"):
            continue
        fan_in += sum(
            1 for e in graph.in_edges(node.id, "CALLS")
            if graph.nodes[e.source].file != file
        )
        fan_out += sum(
            1 for e in graph.out_edges(node.id, "CALLS")
            if graph.nodes[e.target].file != file
        )
    return fan_in, fan_out


def assess_repository(
    graph: RepositoryKnowledgeGraph,
    limit: int | None = None,
    include_tests: bool = False,
) -> list[RiskAssessment]:
    """Score every module, riskiest first.

    Test files are excluded by default: they are not shipped, and their churn
    and complexity would crowd out the production code the caller is asking
    about.
    """
    candidates = graph.file_nodes()
    assessments = [
        assess_file(graph, node.file)
        for node in candidates
        if include_tests or not is_test_file(node.file)
    ]
    assessments.sort(key=lambda a: (-a.risk, a.module))
    return assessments[:limit] if limit else assessments


def explain_risk(assessment: RiskAssessment) -> dict:
    """Flatten an assessment into the mandatory why/signals/edges/evidence shape."""
    return {
        "module": assessment.module,
        "risk": assessment.risk,
        "band": assessment.band,
        "why": assessment.explanation.summary,
        "signals": assessment.explanation.signals,
        "edges": assessment.explanation.edges,
        "evidence": [
            {
                "signal": e.signal,
                "value": e.value,
                "contribution": e.contribution,
                "detail": e.detail,
                "provenance": e.provenance,
            }
            for e in assessment.evidence
        ],
    }
