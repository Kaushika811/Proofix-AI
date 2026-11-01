"""A5.5 — Context Engineering.

Sits between blast-radius analysis (A5) and fix planning (A6). Its only job is to
decide, deterministically, what code the repair actually needs to see, and to
hand that down as a `ContextPackage`.

Hard boundaries, enforced by the absence of the relevant imports:

* **Never calls an LLM.** If a decision here needs judgement, that judgement
  belongs upstream in A4 as evidence, not here as inference.
* **Never generates or mutates code.** It reads the repository and writes one
  JSON artifact to the run store.
* **Never changes RunState.** The package is stored under the run's `context`
  key alongside `sig`, `cve`, `static` and `patches`, which is the established
  pattern for large per-run artifacts.

A5.5 is advisory: if it degrades or fails, A6 and A7 fall back to exactly the
behaviour they had before this layer existed.
"""

from pathlib import Path

from backend.agents.base import AgentBase
from backend.agents.repository_intelligence import load_repository_intelligence
from backend.models.context import ContextPackage
from backend.models.repository_graph import RepositoryIntelligence
from backend.models.sig import SemanticIntentGraph
from backend.services.context_cache import (
    ContextCache,
    build_cache_key,
    root_cause_id,
    sig_hash,
)
from backend.services.context_package import PackageInputs, build_package
from backend.services.context_ranker import RankingInputs, parse_stack_files, rank_files
from backend.services.documentation_index import documentation_signal
from backend.services.git_history_graph import co_change_score
from backend.services.graph_cache import get_knowledge_graph
from backend.services.knowledge_graph import KnowledgeQueryEngine
from backend.services.ownership_graph import ownership_signal
from backend.services.path_resolution import match_key
from backend.services.python_ast_parser import parse_source
from backend.services.repair_memory import repair_signal
from backend.services.repo_layout import is_vendor_path
from backend.services.sig_cache import compute_repo_hash
from backend.state.schema import RunStateModel

CONTEXT_STORE_KEY = "context"


class A55ContextEngineeringAgent(AgentBase):
    agent_id = "A5.5"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Engineering minimal repair context")

        repo = Path(state.repo_clone_path or state.repo_path).resolve()
        sig_data = state.sig or await self.store.get_json(state.run_id, "sig")
        blast = state.blast_graph or {}
        root_cause = state.root_cause or {}
        reproduction = state.reproduction or {}
        static = state.static_report or {}

        sig = self._parse_sig(sig_data)
        intelligence = await self._load_intelligence(state)
        ranking_inputs = self._build_ranking_inputs(
            state, sig, blast, root_cause, reproduction, static, intelligence
        )

        import time

        t_rank = time.monotonic()
        ranked = rank_files(ranking_inputs)
        ranking_ms = int((time.monotonic() - t_rank) * 1000)

        target_file = self._resolve_target_file(ranking_inputs, ranked)
        if not target_file:
            await self.emit_status(
                state,
                "completed",
                "No repair target resolved — A7 keeps its existing context path",
                {"context_engineering": {"skipped": "no_target_file"}},
            )
            return state

        target_function = self._resolve_target_function(repo, target_file, root_cause, reproduction)

        cache = ContextCache(self.store, self.settings.state_ttl_seconds)
        cache_key = build_cache_key(
            repo_hash=compute_repo_hash(repo, state.source_roots or []),
            sig_digest=sig_hash(sig_data),
            target_file=target_file,
            target_function=target_function,
            root_cause_digest=root_cause_id(root_cause),
            attempt=state.retry_count,
        )

        package = await cache.get(state.run_id, cache_key)
        if package is None:
            package = build_package(
                PackageInputs(
                    repo_path=repo,
                    target_file=target_file,
                    target_function=target_function,
                    ranked_files=ranked,
                    root_cause_summary=root_cause.get("root_cause") or root_cause.get("summary") or "",
                    runtime_evidence=self._runtime_evidence(reproduction),
                    acceptance_criteria=self._acceptance_criteria(reproduction, root_cause),
                    contracts=self._contracts(state),
                    validation_requirements=self._validation_requirements(state, reproduction),
                    patch_constraints=self._patch_constraints(state, static),
                    evidence_symbols=self._evidence_symbols(root_cause),
                    budget_chars=self.settings.context_budget_chars,
                    supporting_files=self.settings.context_supporting_files,
                    cache_key=cache_key,
                )
            )
            await cache.set(state.run_id, cache_key, package)

        package.metrics.ranking_time_ms = ranking_ms
        await self.store.set_json(state.run_id, CONTEXT_STORE_KEY, package.to_storage_dict())

        payload = {"context_engineering": self._metrics_payload(package, cache)}
        graph_context = await self._graph_context(intelligence, target_file, target_function)
        if graph_context:
            payload["knowledge_graph_context"] = graph_context

        await self.emit_status(
            state,
            "completed",
            (
                f"Context: {package.metrics.context_functions} function(s) from "
                f"{package.metrics.context_files} file(s), "
                f"{package.metrics.token_reduction:.0%} token reduction"
            ),
            payload,
        )
        return state

    # -- inputs ----------------------------------------------------------

    def _parse_sig(self, sig_data: dict | None) -> SemanticIntentGraph | None:
        if not sig_data:
            return None
        try:
            return SemanticIntentGraph.model_validate(sig_data)
        except Exception:  # noqa: BLE001 — a malformed SIG degrades, never fails
            return None

    def _build_ranking_inputs(
        self,
        state: RunStateModel,
        sig: SemanticIntentGraph | None,
        blast: dict,
        root_cause: dict,
        reproduction: dict,
        static: dict,
        intelligence: RepositoryIntelligence | None = None,
    ) -> RankingInputs:
        traceback_text = reproduction.get("traceback") or reproduction.get("stack_trace") or ""
        mutation = state.mutation_result or {}
        validation_failure = state.validation_failure or {}

        mutation_files = [
            nodeid.split("::")[0]
            for nodeid in (mutation.get("new_failures") or [])
            if nodeid
        ]
        if validation_failure.get("failing_test"):
            mutation_files.append(str(validation_failure["failing_test"]).split("::")[0])

        previously_patched = [
            patch.get("file", "")
            for patch in (state.patch_bundle or {}).get("patches", [])
            if patch.get("file")
        ]

        repository_signals = self._repository_signals(intelligence, self._resolved_target(blast))

        return RankingInputs(
            **repository_signals,
            sig=sig,
            blast_scope=blast.get("scope") or [],
            auto_patch_scope=blast.get("auto_patch_scope") or [],
            origins=blast.get("origins") or [],
            citations=root_cause.get("citations") or [],
            affected_modules=root_cause.get("affected_modules") or [],
            static_findings=static.get("prioritized") or [],
            stack_frames=parse_stack_files(traceback_text),
            failing_file=reproduction.get("failing_file"),
            resolved_target=self._resolved_target(blast),
            target_confidence=self._target_confidence(blast),
            previously_patched=previously_patched,
            mutation_files=[f for f in mutation_files if f],
        )

    async def _load_intelligence(self, state: RunStateModel) -> RepositoryIntelligence | None:
        """Repository index, or None. A5.5 ranks fine without it."""
        if not self.settings.repository_intelligence_enabled:
            return None
        try:
            return await load_repository_intelligence(self.store, state.run_id, self.settings)
        except Exception:  # noqa: BLE001 — a missing index is not an error
            return None

    def _repository_signals(
        self,
        intelligence: RepositoryIntelligence | None,
        target: str | None,
    ) -> dict[str, dict]:
        """Per-file repository-knowledge lookups for the ranker.

        Returns empty lookups when the layer did not run, which makes the ranking
        identical to its pre-Phase-3 behaviour.
        """
        empty: dict[str, dict] = {
            "ownership": {},
            "call_fan_in": {},
            "call_fan_out": {},
            "history_churn": {},
            "co_change": {},
            "prior_repairs": {},
            "documentation": {},
            "graph_related": {},
            "graph_tests": {},
        }
        if intelligence is None:
            return empty

        files = list(intelligence.repository_graph.files)
        fan = {}
        for node in intelligence.call_graph.nodes.values():
            entry = fan.setdefault(node.file, [0, 0])
            entry[0] += node.fan_in
            entry[1] += node.fan_out

        return {
            "ownership": {f: ownership_signal(intelligence.ownership, f) for f in files},
            "call_fan_in": {f: counts[0] for f, counts in fan.items()},
            "call_fan_out": {f: counts[1] for f, counts in fan.items()},
            "history_churn": {f: intelligence.history.churn_for(f) for f in files},
            # Co-change is asked from the target's perspective: "when the target
            # changes, what changes with it?" It is meaningless without a target.
            "co_change": (
                {f: co_change_score(intelligence.history, target, f) for f in files if f != target}
                if target
                else {}
            ),
            "prior_repairs": {f: repair_signal(intelligence.repair_memory, f) for f in files},
            "documentation": {f: documentation_signal(intelligence.documentation, f) for f in files},
            **self._graph_signals(intelligence, target),
        }

    def _graph_signals(self, intelligence, target: str | None) -> dict[str, dict]:
        """Proximity to the target in the unified knowledge graph.

        Two questions the per-structure signals cannot answer: which files are
        *connected* to the target through calls and containment, and which files
        hold tests that actually exercise it. Both are traversal, not inference.
        """
        empty = {"graph_related": {}, "graph_tests": {}}
        if not target or not self.settings.knowledge_graph_enabled:
            return empty

        try:
            graph = get_knowledge_graph(intelligence)
            engine = KnowledgeQueryEngine(graph)
            limit = self.settings.knowledge_graph_max_related

            related: dict[str, float] = {}
            for node in engine.functions_called_by(target, hops=2)[: limit * 4]:
                if node.file and node.file != target:
                    # Nearer callees score higher; the list is already ordered
                    # by hop distance, so position is a faithful proxy.
                    related.setdefault(node.file, round(1.0 - 0.15 * len(related), 4))
            for node in engine.callers_of(target, hops=1)[: limit * 2]:
                if node.file and node.file != target:
                    related.setdefault(node.file, 0.6)

            tests: dict[str, float] = {}
            for node in engine.supporting_tests(target):
                if node.file:
                    tests[node.file] = 1.0

            return {
                "graph_related": {f: v for f, v in related.items() if v > 0},
                "graph_tests": tests,
            }
        except Exception:  # noqa: BLE001 — traversal is advisory, never required
            return empty

    async def _graph_context(self, intelligence, target_file: str, target_function: str | None) -> dict:
        """Supporting material for the package: tests, docs, owners, call chain.

        Metadata for the proof trail and the UI. It is deliberately not injected
        into the patch prompt — repository knowledge is tie-break evidence, and
        a prompt is where primary evidence belongs.
        """
        if not self.settings.knowledge_graph_enabled or intelligence is None:
            return {}
        try:
            graph = get_knowledge_graph(intelligence)
            engine = KnowledgeQueryEngine(graph)
            limit = self.settings.knowledge_graph_max_related

            chain = (
                engine.call_chain(target_file, target_function, hops=2)[:limit]
                if target_function
                else []
            )
            return {
                "supporting_tests": [n.qualname for n in engine.supporting_tests(target_file, target_function)[:limit]],
                "documentation": [n.file for n in engine.documentation_for(target_file, target_function)[:limit]],
                "owners": [n.name for n, _w in engine.owners_of(target_file)[:3]],
                "co_changed": [n.file for n, _w in engine.co_changed_files(target_file, limit)],
                "prior_repairs": [n.name for n in engine.repairs_touching(target_file)[:limit]],
                "call_chain": [f"{n.file}::{n.qualname} (+{d})" for n, d in chain],
            }
        except Exception:  # noqa: BLE001
            return {}

    def _resolved_target(self, blast: dict) -> str | None:
        """A5 pins its resolved target as the first origin and into auto scope."""
        origins = blast.get("origins") or []
        auto = blast.get("auto_patch_scope") or []
        for origin in origins:
            if origin in auto:
                return origin
        return origins[0] if origins else (auto[0] if auto else None)

    def _target_confidence(self, blast: dict) -> float:
        target = self._resolved_target(blast)
        if not target:
            return 0.0
        for scoped in blast.get("scope") or []:
            if scoped.get("path") == target:
                return float(scoped.get("propagation_confidence") or 0.0) or 1.0
        return 1.0

    def _resolve_target_file(self, inputs: RankingInputs, ranked: list) -> str:
        # `resolved_target` comes from A5's blast origins, which already
        # exclude vendor paths (`blast_traversal.resolve_origins`) — this is
        # a second check at the point A5.5 actually trusts the value, so a
        # vendor path can never become the repair target even if some future
        # caller of `RankingInputs` skips that upstream filter.
        if inputs.resolved_target and not is_vendor_path(inputs.resolved_target):
            return inputs.resolved_target
        return ranked[0].file if ranked else ""

    def _resolve_target_function(
        self,
        repo: Path,
        target_file: str,
        root_cause: dict,
        reproduction: dict,
    ) -> str | None:
        """Pick the enclosing function deterministically from evidence line numbers.

        Only AST line containment is used — never name guessing from prose.
        """
        try:
            source = (repo / target_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        parsed = parse_source(source)
        if parsed is None or not parsed.function_spans:
            return None

        for line in self._candidate_lines(target_file, root_cause, reproduction):
            enclosing = [
                span
                for span in parsed.function_spans
                if span.span_start <= line <= span.end_lineno
            ]
            if enclosing:
                # Innermost wins: the span that starts latest still contains it.
                return max(enclosing, key=lambda s: s.span_start).qualname
        return None

    def _candidate_lines(self, target_file: str, root_cause: dict, reproduction: dict) -> list[int]:
        """Evidence line numbers within the target file, strongest first."""
        lines: list[int] = []

        failing_file = reproduction.get("failing_file")
        failing_line = reproduction.get("failing_line")
        if failing_file and failing_line and self._same_file(failing_file, target_file):
            lines.append(int(failing_line))

        citations = root_cause.get("citations") or []
        for verified in (True, False):
            for citation in citations:
                if bool(citation.get("verified")) is not verified:
                    continue
                if citation.get("file") and self._same_file(str(citation["file"]), target_file):
                    try:
                        lines.append(int(citation.get("line") or 0))
                    except (TypeError, ValueError):
                        continue

        return [line for line in lines if line > 0]

    @staticmethod
    def _same_file(candidate: str, target_file: str) -> bool:
        return match_key(candidate, [target_file]) == target_file

    # -- package fields --------------------------------------------------

    def _runtime_evidence(self, reproduction: dict) -> dict:
        return {
            key: reproduction.get(key)
            for key in (
                "status",
                "failing_test",
                "exception_type",
                "exception_message",
                "failing_file",
                "failing_line",
                "traceback",
            )
            if reproduction.get(key) is not None
        }

    def _acceptance_criteria(self, reproduction: dict, root_cause: dict) -> list[str]:
        criteria: list[str] = []
        failing_test = reproduction.get("failing_test")
        if failing_test:
            criteria.append(f"`pytest {failing_test}` must pass after the fix.")
        else:
            criteria.append("The targeted test suite must pass after the fix.")

        criteria.append("No previously passing test may begin to fail.")

        exception_type = reproduction.get("exception_type")
        if exception_type:
            message = reproduction.get("exception_message") or ""
            criteria.append(
                f"The reproduced {exception_type} must no longer occur"
                + (f": {message}" if message else ".")
            )

        summary = root_cause.get("root_cause") or root_cause.get("summary")
        if summary:
            criteria.append(f"The fix must address the identified root cause: {summary}")

        return criteria

    def _contracts(self, state: RunStateModel) -> list[str]:
        return [
            contract.get("assertion", "")
            for contract in (state.patch_bundle or {}).get("contracts", [])
            if contract.get("assertion")
        ]

    def _validation_requirements(self, state: RunStateModel, reproduction: dict) -> list[str]:
        requirements: list[str] = []
        baseline = reproduction.get("pre_existing_failures") or []
        if baseline:
            requirements.append(
                f"{len(baseline)} test(s) already failed before the patch; "
                "these are the accepted baseline and must not grow."
            )
        mutation = state.mutation_result or {}
        if mutation.get("mutant_survived"):
            requirements.append(
                "A previous attempt left a surviving mutant: the tests passed without "
                "actually exercising the fix. The change must be observable by the test."
            )
        retry_brief = state.retry_brief or {}
        if retry_brief.get("assertion_failure"):
            requirements.append(f"Previous failure: {retry_brief['assertion_failure']}")
        return requirements

    def _patch_constraints(self, state: RunStateModel, static: dict) -> list[str]:
        constraints = [
            "Change the minimum necessary to fix the root cause.",
            "Do not reformat or restructure unrelated code.",
        ]
        retry_brief = state.retry_brief or {}
        if retry_brief.get("security_constraint"):
            constraints.append(str(retry_brief["security_constraint"]))
        for finding in (static.get("prioritized") or [])[:3]:
            message = finding.get("message")
            if message:
                constraints.append(f"Must not reintroduce: {message}")
        return constraints

    def _evidence_symbols(self, root_cause: dict) -> tuple[str, ...]:
        """Function names the extractor should force-include, from AST-verified claims."""
        names: list[str] = []
        for citation in root_cause.get("citations") or []:
            symbol = citation.get("symbol")
            if symbol:
                names.append(str(symbol))
        return tuple(dict.fromkeys(names))

    # -- observability ---------------------------------------------------

    def _metrics_payload(self, package: ContextPackage, cache: ContextCache) -> dict:
        metrics = package.metrics
        return {
            "context_files": metrics.context_files,
            "context_functions": metrics.context_functions,
            "context_lines": metrics.context_lines,
            "files_ranked": metrics.files_ranked,
            "files_extracted": metrics.files_extracted,
            "token_reduction": metrics.token_reduction,
            "estimated_prompt_tokens": metrics.estimated_prompt_tokens,
            "estimated_saved_tokens": metrics.estimated_saved_tokens,
            "original_tokens": metrics.original_tokens,
            "reduced_tokens": metrics.reduced_tokens,
            "cache_hit": metrics.cache_hit,
            "ranking_time_ms": metrics.ranking_time_ms,
            "extraction_time_ms": metrics.extraction_time_ms,
            "privacy_time_ms": metrics.privacy_time_ms,
            "build_time_ms": metrics.build_time_ms,
            "privacy_redactions": metrics.privacy_redactions,
            "privacy_guard_status": package.privacy_guard_status,
            "degraded": metrics.degraded,
            "target_file": package.target_file,
            "target_function": package.target_function,
            "cache": cache.metrics.to_dict(),
        }


async def load_context_package(store, run_id: str) -> ContextPackage | None:
    """Read the package A5.5 stored. Returns None when the layer did not run."""
    try:
        raw = await store.get_json(run_id, CONTEXT_STORE_KEY)
    except Exception:  # noqa: BLE001 — consumers must degrade, never fail
        return None
    if not raw:
        return None
    try:
        return ContextPackage.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None
