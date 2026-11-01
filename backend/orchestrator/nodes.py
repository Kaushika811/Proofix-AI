import asyncio

from pathlib import Path

from backend.agents.a0_7_environment import A07EnvironmentAgent
from backend.agents.a1_semantic_mapper import A1SemanticMapperAgent
from backend.agents.a2_dependency_analyzer import A2DependencyAnalyzerAgent
from backend.agents.a3_static_analysis import A3StaticAnalysisAgent
from backend.agents.a3_5_reproduction import A35ReproductionAgent
from backend.agents.a4_evidence_investigator import A4EvidenceInvestigatorAgent
from backend.agents.a5_5_context_engineering import A55ContextEngineeringAgent
from backend.agents.a5_blast_graph import A5BlastGraphAgent
from backend.agents.a6_fix_dag_planner import A6FixDAGPlannerAgent
from backend.agents.a7_code_generation import A7CodeGenerationAgent
from backend.agents.a8_mutation_validator import A8MutationValidatorAgent
from backend.agents.a9_security_rescan import A9SecurityRescanAgent
from backend.agents.a10_mci_scorer import A10MCIScorerAgent
from backend.agents.repository_intelligence import (
    INTELLIGENCE_STORE_KEY,
    RepositoryIntelligenceAgent,
)
from backend.config import Settings
from backend.services.git_service import clone_or_copy_repo, get_head_sha
from backend.services.repair_memory import (
    build_repair_record,
    classify_bug_type,
    record_repair,
)
from backend.services.repo_layout import discover_source_roots
from backend.services.repository_cache import RepositoryCache
from backend.services.sig_helpers import reclassify_cve_report
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel


class GraphNodes:
    def __init__(self, store: RedisStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.repository_intelligence = RepositoryIntelligenceAgent(store, settings)
        self.a07 = A07EnvironmentAgent(store, settings)
        self.a1 = A1SemanticMapperAgent(store, settings)
        self.a2 = A2DependencyAnalyzerAgent(store, settings)
        self.a3 = A3StaticAnalysisAgent(store, settings)
        self.a35 = A35ReproductionAgent(store, settings)
        self.a4 = A4EvidenceInvestigatorAgent(store, settings)
        self.a5 = A5BlastGraphAgent(store, settings)
        self.a55 = A55ContextEngineeringAgent(store, settings)
        self.a6 = A6FixDAGPlannerAgent(store, settings)
        self.a7 = A7CodeGenerationAgent(store, settings)
        self.a8 = A8MutationValidatorAgent(store, settings)
        self.a9 = A9SecurityRescanAgent(store, settings)
        self.a10 = A10MCIScorerAgent(store, settings)

    async def prepare_repo(self, state: RunStateModel) -> RunStateModel:
        if not state.repo_clone_path:
            state.repo_clone_path = clone_or_copy_repo(state.repo_path)
        if not state.source_roots:
            state.source_roots = discover_source_roots(Path(state.repo_clone_path).resolve())
        if not state.base_commit_sha:
            state.base_commit_sha = get_head_sha(Path(state.repo_clone_path))
        await self.store.save_state(state)
        return state

    async def environment_precheck(self, state: RunStateModel) -> RunStateModel:
        """A0.7. Advisory when disabled; gating when on (see `edges.after_environment`).

        Skipped in `stub_mode`, following the convention every other agent uses
        for real tooling (A1's LLM classification, A3's scanners, A4, A6, A7).
        Stub mode exists to exercise the pipeline without invoking real tools,
        and a probe that shells out to an interpreter is exactly that — gating
        on its verdict there would block every stubbed run on the environment of
        the machine running it rather than the repository under test.
        """
        if self.settings.stub_mode or not self.settings.environment_precheck_enabled:
            return state
        state.current_agent = "A0.7"
        await self.store.save_state(state)
        try:
            return await self.a07.run(state)
        except Exception as exc:  # noqa: BLE001
            # A probe that itself fails must not block the run: that would turn
            # a diagnostic into a new failure mode. Record it and continue as
            # the pipeline behaved before the precheck existed.
            state.errors.append({"agent": "A0.7", "error": str(exc)})
            await self.store.save_state(state)
            return state

    async def halt_environment(self, state: RunStateModel) -> RunStateModel:
        """Terminal state for a repository whose tests cannot run.

        Not `failed`: nothing failed. The pipeline determined the target was not
        analysable and declined to spend LLM calls producing empty artifacts.
        `blocked` keeps that distinct in the status, the projection and the
        lifecycle event.
        """
        state.status = "blocked"
        # B-B20: the pointer must name the agent that actually stopped the run,
        # not whatever ran last in `parallel_intel`/`layer1_fan_in` before the
        # gate was checked.
        state.current_agent = "A0.7"
        # No PR decision is written. A run that generated no patch has nothing
        # to route, and an axis-scored decision here would be scoring absence.
        await self.store.save_state(state)
        return state

    async def index_repository(self, state: RunStateModel) -> RunStateModel:
        """Repository Intelligence is advisory: a failure here must not fail the run."""
        if not self.settings.repository_intelligence_enabled:
            return state
        state.current_agent = "A0.5"
        await self.store.save_state(state)
        try:
            state = await self.repository_intelligence.run(state)
        except Exception as exc:  # noqa: BLE001 — degrade to pre-Phase-3 behaviour
            state.errors.append({"agent": "A0.5", "error": str(exc)})
        await self.store.save_state(state)
        return state

    async def parallel_intel(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A1+A2+A3"
        await self.store.save_state(state)

        async def run_a1() -> RunStateModel:
            s = await self.store.load_state(state.run_id)
            assert s
            return await self.a1.run(s)

        async def run_a2() -> RunStateModel:
            s = await self.store.load_state(state.run_id)
            assert s
            return await self.a2.run(s)

        async def run_a3() -> RunStateModel:
            s = await self.store.load_state(state.run_id)
            assert s
            return await self.a3.run(s)

        results = await asyncio.gather(run_a1(), run_a2(), run_a3())
        merged = await self.store.load_state(state.run_id) or state
        for r in results:
            if r.sig:
                merged.sig = r.sig
            if r.cve_report:
                merged.cve_report = r.cve_report
            if r.static_report:
                merged.static_report = r.static_report
            merged.ws_sequence = max(merged.ws_sequence, r.ws_sequence)
        await self.store.save_state(merged)
        return merged

    async def layer1_fan_in(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "fan-in"
        sig = state.sig or await self.store.get_json(state.run_id, "sig")
        cve = state.cve_report or await self.store.get_json(state.run_id, "cve")
        if cve:
            reclassified = reclassify_cve_report(sig, cve)
            state.cve_report = reclassified
            await self.store.set_json(state.run_id, "cve", reclassified)
        await self.store.save_state(state)
        return state

    async def reproduction_gate(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A3.5"
        await self.store.save_state(state)
        return await self.a35.run(state)

    async def investigate(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A4"
        await self.store.save_state(state)
        return await self.a4.run(state)

    async def blast_scope(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A5"
        await self.store.save_state(state)
        return await self.a5.run(state)

    async def engineer_context(self, state: RunStateModel) -> RunStateModel:
        """A5.5 is advisory: a failure here must not fail the run."""
        if not self.settings.context_engineering_enabled:
            return state
        state.current_agent = "A5.5"
        await self.store.save_state(state)
        try:
            return await self.a55.run(state)
        except Exception as exc:  # noqa: BLE001 — degrade to pre-A5.5 behaviour
            state.errors.append({"agent": "A5.5", "error": str(exc)})
            return state

    async def plan_fixes(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A6"
        await self.store.save_state(state)
        return await self.a6.run(state)

    async def generate_code(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A7"
        await self.store.save_state(state)
        return await self.a7.run(state)

    async def validate_mutation(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A8"
        await self.store.save_state(state)
        return await self.a8.run(state)

    async def validate_security(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A9"
        await self.store.save_state(state)
        return await self.a9.run(state)

    async def route_pr(self, state: RunStateModel) -> RunStateModel:
        state.current_agent = "A10"
        await self.store.save_state(state)
        state = await self.a10.run(state)
        # B-B20: the pointer must name the agent that actually finished the
        # run, not "fan-in" from `layer1_fan_in` several nodes back.
        state.current_agent = "A10"
        await self.store.save_state(state)
        await self._remember_repairs(state)
        self._learn_from_run(state)
        return state

    def _learn_from_run(self, state: RunStateModel) -> None:
        """Extract the completed run into durable organisational knowledge.

        Placed here, after routing, for the same reason repair memory is: the
        outcome is only known once the trust gates have decided. Synchronous
        because the whole update is in-memory counting and stays well inside
        its budget; making it a task would add ordering hazards for no gain.
        """
        if not self.settings.learning_enabled:
            return
        try:
            from backend.services.learning_pipeline import get_learning_pipeline

            get_learning_pipeline(self.settings, self.store).learn_from_run(state)
        except Exception as exc:  # noqa: BLE001 — learning never fails a run
            state.errors.append({"agent": "learning", "error": str(exc)})

    async def _remember_repairs(self, state: RunStateModel) -> None:
        """Write this run's validated repairs into persistent repair memory.

        Placed here rather than inside A10 so the scoring agent keeps its single
        responsibility, and so the write happens exactly once, after routing has
        decided whether the repair was actually trustworthy.

        Only validated repairs are stored. A patch that failed validation is not
        knowledge about how to fix this repository; recording it would poison
        every future retrieval.
        """
        if not (
            self.settings.repository_intelligence_enabled
            and self.settings.repair_memory_enabled
        ):
            return

        try:
            pointer = await self.store.get_json(state.run_id, INTELLIGENCE_STORE_KEY)
            if not pointer or not pointer.get("repository_id"):
                return

            mutation = state.mutation_result or {}
            security = state.security_result or {}
            decision = state.pr_decision or {}
            patches = (state.patch_bundle or {}).get("patches") or []

            validated = bool(mutation.get("pytest_passed")) and not security.get("rejected")
            if not validated or not patches:
                return

            repo = Path(state.repo_clone_path or state.repo_path).resolve()
            bug_type = classify_bug_type(
                state.reproduction,
                state.root_cause,
                (state.static_report or {}).get("prioritized"),
            )
            root_cause_summary = (state.root_cause or {}).get("root_cause") or ""
            affected = [p.get("file", "") for p in patches if p.get("file")]

            cache = RepositoryCache(
                self.store,
                ttl_seconds=self.settings.state_ttl_seconds,
                version=self.settings.repository_cache_version,
            )
            memory = await cache.load_repair_memory(pointer["repository_id"])
            memory.repository_id = pointer["repository_id"]

            for patch in patches:
                file_path = patch.get("file")
                if not file_path:
                    continue
                record_repair(
                    memory,
                    build_repair_record(
                        run_id=state.run_id,
                        repo_path=repo,
                        repository_hash=pointer.get("repository_hash", ""),
                        file=file_path,
                        function=None,
                        bug_type=bug_type,
                        root_cause_summary=root_cause_summary,
                        affected_files=affected,
                        validation_passed=True,
                        mutation_score=mutation.get("mutation_score"),
                        mutation_status=mutation.get("mutation_status") or "not_run",
                        security_score=security.get("security_score"),
                        retry_count=state.retry_count,
                        pr_type=decision.get("pr_type", ""),
                        accepted_patch_diff=(state.patch_bundle or {}).get("diff_text", ""),
                        # Hashes describe the pre-patch code, which is what a
                        # future run will be looking at when it queries.
                        original_file_source=patch.get("original") or "",
                    ),
                )

            await cache.save_repair_memory(memory)
        except Exception as exc:  # noqa: BLE001 — memory is advisory, never fatal
            state.errors.append({"agent": "repair_memory", "error": str(exc)})
