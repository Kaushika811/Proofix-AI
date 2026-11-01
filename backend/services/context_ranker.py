"""Deterministic relevance ranking over candidate repair files.

Every signal is an additive contribution with a named weight declared at module
level, mirroring the convention already used by `root_cause_builder` and
`blast_traversal`. No LLM is consulted, no randomness is involved, and ties break
on a total order, so the same run state against the same commit always produces
the same ranking.

Each returned file carries its `signals` breakdown and human-readable `evidence`,
so "why did the model see this file?" is answerable from the stored package.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from backend.models.context import RankedContextFile
from backend.models.sig import SemanticIntentGraph
from backend.services.path_resolution import normalize_path_token
from backend.services.repo_layout import is_vendor_path

RANKING_VERSION = "v1"

# -- signal weights --------------------------------------------------------
# Ordered by how directly the signal implicates a file in the observed failure.
# Runtime facts outrank static inference, which outranks graph proximity, which
# outranks repository-history priors.

W_TARGET_RESOLUTION = 1.00      # the resolved patch target
W_RUNTIME_STACK = 0.90          # a non-library frame in the reproduction traceback
W_STACK_DEPTH_DECAY = 0.85      # per frame away from the innermost application frame
W_REPRODUCTION_EVIDENCE = 0.60  # the failing file recorded by A3.5
W_ROOT_CAUSE_VERIFIED = 0.80    # a verified A4 citation
W_ROOT_CAUSE_UNVERIFIED = 0.20  # an unverified A4 citation
W_ROOT_CAUSE_MODULE = 0.25      # named in root_cause.affected_modules
W_STATIC_FINDING = 0.30         # a prioritized A3 finding, scaled by severity
W_MUTATION_RELEVANCE = 0.45     # implicated by validation failure or prior patch
W_RECENT_MODIFICATION = 0.35    # patched earlier in this run (retry loop)
W_SIG_DISTANCE = 0.30           # blast propagation confidence
W_IMPORT_DISTANCE = 0.25        # import-graph hops from an origin
W_CALL_GRAPH_DISTANCE = 0.20    # reachable via the SIG edge list
W_DEPENDENCY_CENTRALITY = 0.15  # fan-in, saturating
W_GIT_CHURN = 0.20              # bug-fix commit density from the SIG
W_SHARED_UTILITY_PENALTY = -0.40  # widely imported generic helper

# -- repository intelligence signals (Phase 3) -----------------------------
# Long-lived repository knowledge rather than facts about this failure, so every
# weight here is deliberately small. All of them are zero when the intelligence
# layer is absent, so ranking without it is byte-identical to its pre-Phase-3
# behaviour.
#
# Individually small weights are not enough: seven of them summing freely reach
# 0.95, which would overturn a traceback frame at W_RUNTIME_STACK. Their total
# per file is therefore capped below W_REPRODUCTION_EVIDENCE, which pins them to
# the role they are meant to play — breaking ties between files that runtime
# evidence has already judged comparable, never overturning that evidence.

W_PRIOR_REPAIR = 0.25       # this file has been successfully repaired before
W_CO_CHANGE = 0.20          # historically changes alongside the resolved target
W_HISTORY_CHURN = 0.15      # bug-fix churn from the full history graph
W_CALL_FAN_IN = 0.12        # inbound calls, saturating
W_OWNERSHIP = 0.10          # concentrated, recently active ownership
W_CALL_FAN_OUT = 0.08       # outbound calls, saturating
W_DOCUMENTATION = 0.05      # the module is documented
W_GRAPH_RELATED = 0.18      # near the target in the knowledge graph
W_GRAPH_VALIDATED = 0.12    # contains a test that exercises the target

# Ceiling on the summed repository-intelligence contribution to any one file.
# Below W_REPRODUCTION_EVIDENCE (0.60) and well below W_RUNTIME_STACK (0.90).
MAX_REPOSITORY_INTELLIGENCE_CONTRIBUTION = 0.45

# Fan-in above which a file is treated as a shared utility rather than a
# suspect. Generic helpers are imported everywhere and are rarely the cause.
SHARED_UTILITY_FANIN = 8
CENTRALITY_SATURATION = 10.0

# Call counts saturate: the difference between 20 and 200 callers says nothing
# more about culpability than the difference between 2 and 20.
CALL_FANIN_SATURATION = 20.0
CALL_FANOUT_SATURATION = 15.0

# Frames from these paths are runtime noise, not application code.
_LIBRARY_MARKERS = ("site-packages", "dist-packages", "/lib/python", "_pytest", "/pytest")

MAX_STACK_FRAMES = 12


@dataclass
class RankingInputs:
    """Everything the ranker reads. Plain data — no RunState dependency."""

    sig: SemanticIntentGraph | None = None
    blast_scope: list[dict] = field(default_factory=list)
    auto_patch_scope: list[str] = field(default_factory=list)
    origins: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    static_findings: list[dict] = field(default_factory=list)
    stack_frames: list[str] = field(default_factory=list)
    failing_file: str | None = None
    resolved_target: str | None = None
    target_confidence: float = 0.0
    previously_patched: list[str] = field(default_factory=list)
    mutation_files: list[str] = field(default_factory=list)

    # Repository intelligence, supplied as plain per-file lookups so the ranker
    # stays free of any dependency on the Phase 3 models. Empty means the layer
    # did not run, and every signal below contributes nothing.
    ownership: dict[str, float] = field(default_factory=dict)
    call_fan_in: dict[str, int] = field(default_factory=dict)
    call_fan_out: dict[str, int] = field(default_factory=dict)
    history_churn: dict[str, float] = field(default_factory=dict)
    co_change: dict[str, float] = field(default_factory=dict)
    prior_repairs: dict[str, float] = field(default_factory=dict)
    documentation: dict[str, float] = field(default_factory=dict)
    # Knowledge-graph proximity, scored inside the same capped group so graph
    # traversal can never outrank a traceback either.
    graph_related: dict[str, float] = field(default_factory=dict)
    graph_tests: dict[str, float] = field(default_factory=dict)


def parse_stack_files(traceback_text: str) -> list[str]:
    """Application file paths from a traceback, innermost last.

    Uses the AST-free but structural `File "...", line N` shape that CPython
    emits; library frames are dropped so they cannot outrank application code.
    """
    if not traceback_text:
        return []

    files: list[str] = []
    for raw_line in traceback_text.splitlines():
        line = raw_line.strip()
        if not line.startswith('File "'):
            continue
        rest = line[len('File "') :]
        end = rest.find('"')
        if end <= 0:
            continue
        path = rest[:end]
        if any(marker in path for marker in _LIBRARY_MARKERS):
            continue
        files.append(path)

    return files[-MAX_STACK_FRAMES:]


def _match(path: str, candidates: dict[str, str]) -> str | None:
    """Map an arbitrary path reference onto a candidate key."""
    token = normalize_path_token(path)
    if not token:
        return None
    if token in candidates:
        return candidates[token]

    base = PurePosixPath(token).name
    suffix_hits = [key for tok, key in candidates.items() if tok.endswith(f"/{token}")]
    if len(suffix_hits) == 1:
        return suffix_hits[0]
    base_hits = [key for tok, key in candidates.items() if tok == base or tok.endswith(f"/{base}")]
    if len(base_hits) == 1:
        return base_hits[0]
    return None


class _Accumulator:
    """Per-file running score with its provenance."""

    def __init__(self) -> None:
        self.signals: dict[str, float] = {}
        self.evidence: list[str] = []

    def add(self, signal: str, value: float, note: str) -> None:
        if value == 0.0:
            return
        self.signals[signal] = round(self.signals.get(signal, 0.0) + value, 4)
        if note not in self.evidence:
            self.evidence.append(note)

    @property
    def score(self) -> float:
        return round(sum(self.signals.values()), 4)


def rank_files(inputs: RankingInputs) -> list[RankedContextFile]:
    """Score every candidate file. Returns descending by score, deterministically."""
    candidates: set[str] = set(inputs.auto_patch_scope)
    candidates.update(s.get("path", "") for s in inputs.blast_scope if s.get("path"))
    candidates.update(inputs.origins)
    if inputs.resolved_target:
        candidates.add(inputs.resolved_target)
    if inputs.sig:
        # Citations and stack frames may name files outside the blast scope.
        for raw in [c.get("file", "") for c in inputs.citations] + inputs.stack_frames:
            matched = _match(raw, {normalize_path_token(p): p for p in inputs.sig.files})
            if matched:
                candidates.add(matched)
    candidates.discard("")

    # Belt-and-suspenders: upstream (blast origin resolution, the SIG itself)
    # already excludes vendor/VCS/cache/build directories, but this is the
    # one place every candidate source converges before a file can enter the
    # repair context. A candidate reaching here from `.venv`, `node_modules`,
    # or `site-packages` — however it got in — must never be ranked or
    # extracted as if it were the repository's own code.
    candidates = {path for path in candidates if not is_vendor_path(path)}

    if not candidates:
        return []

    lookup = {normalize_path_token(path): path for path in candidates}
    acc: dict[str, _Accumulator] = {path: _Accumulator() for path in candidates}

    _score_target(inputs, lookup, acc)
    _score_runtime(inputs, lookup, acc)
    _score_root_cause(inputs, lookup, acc)
    _score_static(inputs, lookup, acc)
    _score_retry(inputs, lookup, acc)
    _score_graph(inputs, lookup, acc)
    _score_repository_priors(inputs, acc)
    _score_repository_intelligence(inputs, lookup, acc)

    ranked: list[RankedContextFile] = []
    for path, accumulator in acc.items():
        ranked.append(
            RankedContextFile(
                file=path,
                score=accumulator.score,
                reason=accumulator.evidence[0] if accumulator.evidence else "in blast scope",
                evidence=list(accumulator.evidence),
                confidence=_confidence(accumulator),
                signals=dict(accumulator.signals),
                is_target=(path == inputs.resolved_target),
            )
        )

    # Total order: score desc, target first on ties, then path for stability.
    ranked.sort(key=lambda f: (-f.score, not f.is_target, f.file))
    return ranked


def _confidence(accumulator: _Accumulator) -> float:
    """Squash an unbounded additive score into 0..1 for reporting."""
    positive = sum(v for v in accumulator.signals.values() if v > 0)
    if positive <= 0:
        return 0.0
    return round(min(1.0, 1.0 - math.exp(-positive)), 4)


def _score_target(inputs: RankingInputs, lookup: dict[str, str], acc: dict[str, _Accumulator]) -> None:
    if not inputs.resolved_target:
        return
    key = _match(inputs.resolved_target, lookup)
    if key and key in acc:
        acc[key].add(
            "target_resolution",
            W_TARGET_RESOLUTION * max(inputs.target_confidence, 0.0),
            f"resolved patch target (confidence {inputs.target_confidence:.2f})",
        )


def _score_runtime(inputs: RankingInputs, lookup: dict[str, str], acc: dict[str, _Accumulator]) -> None:
    # Innermost frame is last; weight decays outward.
    for depth, frame in enumerate(reversed(inputs.stack_frames)):
        key = _match(frame, lookup)
        if not key or key not in acc:
            continue
        acc[key].add(
            "runtime_stack",
            W_RUNTIME_STACK * (W_STACK_DEPTH_DECAY**depth),
            f"appears in reproduction traceback (frame depth {depth})",
        )

    if inputs.failing_file:
        key = _match(inputs.failing_file, lookup)
        if key and key in acc:
            acc[key].add(
                "reproduction_evidence",
                W_REPRODUCTION_EVIDENCE,
                "recorded as the failing file by reproduction",
            )


def _score_root_cause(inputs: RankingInputs, lookup: dict[str, str], acc: dict[str, _Accumulator]) -> None:
    for citation in inputs.citations:
        key = _match(str(citation.get("file", "")), lookup)
        if not key or key not in acc:
            continue
        if citation.get("verified"):
            acc[key].add("root_cause_confidence", W_ROOT_CAUSE_VERIFIED, "verified root-cause citation")
        else:
            acc[key].add("root_cause_confidence", W_ROOT_CAUSE_UNVERIFIED, "unverified root-cause citation")

    for module in inputs.affected_modules:
        key = _match(str(module), lookup)
        if key and key in acc:
            acc[key].add("root_cause_confidence", W_ROOT_CAUSE_MODULE, "named in root-cause affected modules")


def _score_static(inputs: RankingInputs, lookup: dict[str, str], acc: dict[str, _Accumulator]) -> None:
    for finding in inputs.static_findings:
        key = _match(str(finding.get("file", "")), lookup)
        if not key or key not in acc:
            continue
        severity = float(finding.get("severity") or 0.5)
        acc[key].add(
            "static_finding",
            W_STATIC_FINDING * max(0.0, min(1.0, severity)),
            f"static finding (severity {severity:.2f})",
        )


def _score_retry(inputs: RankingInputs, lookup: dict[str, str], acc: dict[str, _Accumulator]) -> None:
    for path in inputs.previously_patched:
        key = _match(path, lookup)
        if key and key in acc:
            acc[key].add("recent_modification", W_RECENT_MODIFICATION, "modified by an earlier attempt in this run")

    for path in inputs.mutation_files:
        key = _match(path, lookup)
        if key and key in acc:
            acc[key].add("mutation_relevance", W_MUTATION_RELEVANCE, "implicated by validation failure")


def _score_graph(inputs: RankingInputs, lookup: dict[str, str], acc: dict[str, _Accumulator]) -> None:
    for scoped in inputs.blast_scope:
        path = scoped.get("path", "")
        if path not in acc:
            continue
        propagation = float(scoped.get("propagation_confidence") or 0.0)
        hops = int(scoped.get("hop_count") or 0)
        acc[path].add(
            "sig_distance",
            W_SIG_DISTANCE * propagation,
            f"blast propagation {propagation:.2f} at {hops} hop(s)",
        )
        if hops > 0:
            acc[path].add(
                "import_distance",
                W_IMPORT_DISTANCE / (1 + hops),
                f"{hops} import hop(s) from an origin",
            )

    if inputs.sig:
        origin_tokens = {normalize_path_token(o) for o in inputs.origins}
        for source, _target_module in inputs.sig.edges:
            if normalize_path_token(source) not in origin_tokens:
                continue
            key = _match(source, lookup)
            if key and key in acc:
                acc[key].add("call_graph_distance", W_CALL_GRAPH_DISTANCE, "origin of a SIG edge")


def _score_repository_intelligence(
    inputs: RankingInputs,
    lookup: dict[str, str],
    acc: dict[str, _Accumulator],
) -> None:
    """Apply the Phase 3 repository-knowledge signals, capped as a group.

    Contributions are staged per file first, then scaled down together if their
    sum exceeds MAX_REPOSITORY_INTELLIGENCE_CONTRIBUTION. Scaling the group
    rather than clipping individual signals preserves their relative weighting,
    so the breakdown still explains which prior mattered most.
    """
    staged: dict[str, list[tuple[str, float, str]]] = {}

    def stage(raw: str, signal: str, value: float, note: str) -> None:
        if value <= 0:
            return
        key = _match(raw, lookup)
        if key and key in acc:
            staged.setdefault(key, []).append((signal, value, note))

    for raw, prior in inputs.prior_repairs.items():
        stage(
            raw,
            "prior_repair",
            W_PRIOR_REPAIR * min(1.0, prior),
            "repaired successfully in an earlier run",
        )

    for raw, score in inputs.co_change.items():
        stage(
            raw,
            "co_change",
            W_CO_CHANGE * min(1.0, score),
            f"changes alongside the target in {min(1.0, score):.0%} of its commits",
        )

    for raw, churn in inputs.history_churn.items():
        stage(
            raw,
            "history_churn",
            W_HISTORY_CHURN * min(1.0, churn),
            f"bug-fix churn {churn:.2f} across indexed history",
        )

    for raw, fan_in in inputs.call_fan_in.items():
        stage(
            raw,
            "call_fan_in",
            W_CALL_FAN_IN * min(1.0, fan_in / CALL_FANIN_SATURATION),
            f"{fan_in} inbound call(s) in the repository call graph",
        )

    for raw, fan_out in inputs.call_fan_out.items():
        stage(
            raw,
            "call_fan_out",
            W_CALL_FAN_OUT * min(1.0, fan_out / CALL_FANOUT_SATURATION),
            f"{fan_out} outbound call(s) in the repository call graph",
        )

    for raw, owner_score in inputs.ownership.items():
        stage(
            raw,
            "ownership",
            W_OWNERSHIP * min(1.0, owner_score),
            f"actively maintained (ownership signal {min(1.0, owner_score):.2f})",
        )

    for raw, relevance in inputs.documentation.items():
        stage(
            raw,
            "documentation",
            W_DOCUMENTATION * min(1.0, relevance),
            "referenced by repository documentation",
        )

    for raw, proximity in inputs.graph_related.items():
        stage(
            raw,
            "graph_related",
            W_GRAPH_RELATED * min(1.0, proximity),
            f"connected to the target in the knowledge graph (proximity {min(1.0, proximity):.2f})",
        )

    for raw, strength in inputs.graph_tests.items():
        stage(
            raw,
            "graph_validated",
            W_GRAPH_VALIDATED * min(1.0, strength),
            "contains a test that exercises the target",
        )

    for key, contributions in staged.items():
        total = sum(value for _signal, value, _note in contributions)
        scale = (
            MAX_REPOSITORY_INTELLIGENCE_CONTRIBUTION / total
            if total > MAX_REPOSITORY_INTELLIGENCE_CONTRIBUTION
            else 1.0
        )
        for signal, value, note in contributions:
            acc[key].add(signal, round(value * scale, 4), note)


def _score_repository_priors(inputs: RankingInputs, acc: dict[str, _Accumulator]) -> None:
    if not inputs.sig:
        return
    for path, accumulator in acc.items():
        node = inputs.sig.files.get(path)
        if node is None:
            continue

        if node.churn_weight:
            accumulator.add(
                "git_churn",
                W_GIT_CHURN * node.churn_weight,
                f"bug-fix churn weight {node.churn_weight:.2f}",
            )

        fan_in = len(node.imported_by)
        if fan_in:
            accumulator.add(
                "dependency_centrality",
                W_DEPENDENCY_CENTRALITY * min(1.0, fan_in / CENTRALITY_SATURATION),
                f"imported by {fan_in} module(s)",
            )

        # A generic helper reached by most of the repository is usually context
        # noise rather than the defect, unless runtime evidence says otherwise.
        if fan_in >= SHARED_UTILITY_FANIN and node.role == "internal-util":
            has_runtime = any(
                accumulator.signals.get(signal)
                for signal in ("runtime_stack", "reproduction_evidence", "target_resolution")
            )
            if not has_runtime:
                accumulator.add(
                    "shared_utility_penalty",
                    W_SHARED_UTILITY_PENALTY,
                    f"shared utility with {fan_in} dependents and no runtime evidence",
                )
