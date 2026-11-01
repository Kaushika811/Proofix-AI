from backend.services.repair_step_evidence import patch_fingerprint

import ast
from pathlib import Path

from backend.agents.a7_patch_engine import (
    PatchLLMOutput,
    apply_stub_plan,
    build_llm_prompt,
    build_patch_plans,
)
from backend.agents.a5_5_context_engineering import load_context_package
from backend.agents.base import AgentBase
from backend.agents.repository_intelligence import load_repository_intelligence
from backend.models.blast import BlastGraphResult
from backend.models.context import ContextPackage
from backend.models.patch import BehavioralContract, PatchBundle, PatchCandidate, PatchPlan
from backend.models.root_cause import RootCauseBrief
from backend.models.validation import RetryBrief
from backend.services.git_service import get_style_exemplar
from backend.services.issue_targeting import resolved_issue_target, issue_repair_scope
from backend.services.llm import LLMService
from backend.services.mci_verifier import generate_diff_from_patches
from backend.models.repository_graph import RepairQuery
from backend.services.repair_memory import (
    classify_bug_type,
    content_hash,
    find_similar_repairs,
    summarize_matches,
)
from backend.services.retry_brief_builder import retry_reason_from_brief
from backend.services.evidence_recovery import (
    contract_hash, record_rejected_duplicate, repeats_failed_patch,
)
from backend.services.runtime_patch_prompt import (
    COMPLETE_FILE_RETRY_INSTRUCTION,
    build_retry_prompt_section,
    build_runtime_patch_prompt,
    changed_python_symbols,
    extract_relevant_code,
    has_semantic_diff,
    uses_runtime_prompt,
    validate_patch_integrity,
)
from backend.state.schema import RunStateModel


class A7CodeGenerationAgent(AgentBase):
    agent_id = "A7"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Generating validated patches")
        state.recovery_blocked_reason = None
        if state.recovery_contract_hash and contract_hash(state) != state.recovery_contract_hash:
            state.recovery_blocked_reason = "Issue contract or acceptance test changed during recovery"
            await self.emit_status(state, "failed", state.recovery_blocked_reason)
            return state
        repo = Path(state.repo_clone_path or state.repo_path).resolve()
        acquired = await self.store.acquire_lock(state.run_id)

        metrics: list[dict] = []
        written: dict[Path, str] = {}

        try:
            if not acquired:
                state.recovery_blocked_reason = "Could not acquire patch lock"
                await self.emit_status(state, "failed", "Could not acquire patch lock")
                return state

            context_package = await load_context_package(self.store, state.run_id)
            intelligence = await self._load_intelligence(state)
            self._repository_id_cache = _learning_repository_id(state)
            root_cause = self._parse_root_cause(state.root_cause or {})
            blast = self._parse_blast_graph(state.blast_graph or {})
            fix_dag = state.fix_dag or {}
            retry_brief = self._parse_retry_brief(state.retry_brief)

            scope_files = self._resolve_scope_files(blast, state)
            if state.issue_contract:
                target = resolved_issue_target(state)
                # A targeted run must never patch a static finding that is
                # unrelated to its selected failing test.
                planned = {f for node in fix_dag.get("nodes", []) for f in node.get("files", [])}
                allowed = issue_repair_scope(state)
                scope_files = [f for f in allowed if f in scope_files and (f == target or f in planned)]
            scope_files = self._order_scope_files(scope_files, fix_dag)
            file_sources = self._load_file_sources(repo, scope_files, state)
            plans = build_patch_plans(
                scope_files,
                root_cause,
                blast,
                retry_brief,
                reproduction=state.reproduction,
                repo_path=repo,
                file_sources=file_sources,
                issue_contract=state.issue_contract,
            )

            patches: list[PatchCandidate] = []
            contracts: list[BehavioralContract] = []
            exemplar_commit = None

            # Every file this invocation writes, with the content it had first.
            #
            # A plan producing no patch is ordinary — most scope files need no
            # change, and the loop skips them — so that must never trigger a
            # restore. What must is an **exception** partway through: the writes
            # already on disk are real, `state.patch_bundle` is never set, and
            # A8 then validates a clone carrying changes no bundle records and
            # no scoring accounts for. Rolling back leaves the clone exactly as
            # A7 found it, which is the only state A8 can reason about.
            for plan in plans:
                full = repo / plan.file
                if not full.resolve().is_relative_to(repo):
                    raise ValueError("Repair plan path escapes the repository")
                if not full.exists():
                    continue

                # Renew before each plan's LLM calls rather than trusting one
                # lease to outlast every plan. Losing the lease means another
                # writer may already be in this clone, so stop writing — with
                # whatever earlier plans produced kept, since those writes were
                # made while the lock was genuinely held.
                if not await self._renew_lock(state.run_id):
                    await self.emit_status(
                        state,
                        "failed",
                        f"Patch lock lease lost before {plan.file}; stopping to avoid a concurrent write",
                    )
                    break

                original = self._resolve_original_baseline(full, plan.file, state)
                commit, exemplar = get_style_exemplar(repo, plan.file)
                if commit:
                    exemplar_commit = commit

                previous_patch = self._previous_patch_for_file(state, plan.file)
                llm_output, plan_metrics = await self._generate_from_plan(
                    plan,
                    original,
                    exemplar,
                    retry_brief,
                    state.mutation_result,
                    previous_patch,
                    repo,
                    state.retry_count,
                    context_package,
                    state.run_id,
                )
                # Metadata only: recorded alongside the attempt, never added to
                # the prompt. See `_repair_matches`.
                prior = self._repair_matches(intelligence, plan, original, state)
                if prior:
                    plan_metrics["prior_repairs"] = prior
                metrics.append(plan_metrics)

                if llm_output is None:
                    await self.emit_status(
                        state,
                        "failed",
                        f"No semantic patch generated for {plan.file}",
                        {"a7_patch_metrics": plan_metrics},
                    )
                    continue

                ok, integrity_reason = validate_patch_integrity(
                    original, llm_output.patched_content, file=plan.file,
                    target_symbol=getattr(plan, "target_function", None),
                )
                if not ok:
                    plan_metrics["retry_reason"] = integrity_reason
                    plan_metrics["semantic_diff"] = False
                    await self.emit_status(
                        state,
                        "failed",
                        f"Patch integrity check failed for {plan.file}: {integrity_reason}",
                        {"a7_patch_metrics": plan_metrics},
                    )
                    continue

                if not self._validate_python(llm_output.patched_content):
                    await self.emit_status(state, "failed", f"Invalid Python in {plan.file}")
                    continue

                # The original was written back immediately before the patch —
                # two writes where one suffices, the first of which only ever
                # produced a window where the file held content nobody wanted.
                written.setdefault(full, full.read_text(encoding="utf-8"))
                full.write_text(llm_output.patched_content, encoding="utf-8")

                patches.append(
                    PatchCandidate(
                        file=plan.file,
                        original=original,
                        patched=llm_output.patched_content,
                        method="ast_validated_write",
                        target_symbol=getattr(plan, "target_function", None),
                        changed_symbols=sorted(changed_python_symbols(original, llm_output.patched_content))
                        if plan.file.endswith(".py") else [],
                    )
                )
                contracts.append(
                    BehavioralContract(
                        assertion=llm_output.contract_assertion,
                        location=llm_output.contract_location or plan.file,
                    )
                )

            planned_files = {plan.file for plan in plans}
            required_files = {
                file_path for node in fix_dag.get("nodes") or []
                for file_path in node.get("files") or [] if file_path in planned_files
            }
            if state.issue_contract:
                required_files.update(planned_files)
            patched_files = {patch.file for patch in patches}
            missing = sorted(required_files - patched_files)
            if not patches or missing:
                self._rollback(written)
                state.patch_bundle = None
                reason = (
                    f"Required repair plan files have no substantive patch: {', '.join(missing)}"
                    if missing else "No substantive patch was generated"
                )
                state.recovery_blocked_reason = reason
                await self.emit_status(state, "failed", reason)
                return state

            issue_id = (fix_dag.get("execution_order") or ["fix-0"])[0]
            diff_text = generate_diff_from_patches([p.model_dump() for p in patches])
            bundle = PatchBundle(
                issue_id=issue_id,
                patches=patches,
                contracts=contracts,
                style_exemplar_commit=exemplar_commit,
                diff_text=diff_text,
            )
            bundle_dict = bundle.model_dump(mode="json")
            if repeats_failed_patch(bundle_dict, state.recovery_attempts):
                self._rollback(written)
                record_rejected_duplicate(state, bundle_dict)
                await self.emit_status(state, "failed", state.recovery_blocked_reason)
                return state
            bundle_dict["application_evidence"] = {
                "patch_hash": patch_fingerprint(bundle_dict),
                "files": {
                    patch.file: {
                        "targetFunction": patch.target_symbol,
                        "expectedOutcome": next((p.required_behavior_change for p in plans if p.file == patch.file), ""),
                        "diff": generate_diff_from_patches([patch.model_dump()]),
                    } for patch in patches
                },
            }
            await self.store.set_json(state.run_id, "patches", bundle_dict)
            state.patch_bundle = bundle_dict

            payload = {
                "files": [p.file for p in patches],
                "plan_count": len(plans),
            }
            if metrics:
                payload["a7_patch_metrics"] = metrics[0] if len(metrics) == 1 else metrics

            await self.emit_status(
                state,
                "completed",
                f"Generated {len(patches)} patches from {len(plans)} plans",
                payload,
            )
        except Exception as exc:  # noqa: BLE001 — restored, reported, re-raised
            restored = self._rollback(written)
            state.errors.append(
                {"agent": self.agent_id, "error": str(exc), "rolled_back": restored}
            )
            await self.emit_status(
                state,
                "failed",
                (
                    f"Patch generation failed: {exc}. "
                    f"Rolled back {len(restored)} partially patched file(s)."
                    if restored
                    else f"Patch generation failed: {exc}. No files had been written."
                ),
                {"rolled_back": restored},
            )
            raise
        finally:
            if acquired:
                await self.store.release_lock(state.run_id)
        return state

    async def _renew_lock(self, run_id: str) -> bool:
        """Extend the patch lease, tolerating a store that cannot renew.

        `renew_lock` is newer than the stores in the test suite and than any
        fake a caller may inject. A store without it is treated as holding the
        lease — the pre-existing behaviour — rather than failing a run over a
        capability that did not exist before.
        """
        renew = getattr(self.store, "renew_lock", None)
        if renew is None:
            return True
        return bool(await renew(run_id))

    @staticmethod
    def _rollback(written: dict[Path, str]) -> list[str]:
        """Restore every file this invocation wrote. Returns what was restored.

        Best-effort by construction: a restore that itself fails must not mask
        the original exception, which is the thing worth reporting. A file that
        could not be restored is left out of the returned list, so the caller
        reports what actually happened rather than what was attempted.
        """
        restored: list[str] = []
        for path, original in written.items():
            try:
                path.write_text(original, encoding="utf-8")
                restored.append(str(path))
            except OSError:
                continue
        return restored

    def _learned_conventions(self, plan: PatchPlan) -> str:
        """Repository conventions learned from prior repairs, or "".

        Never raises and never blocks: a learning fault costs A7 some context,
        which is exactly the pre-Phase-6 behaviour.
        """
        if not (
            self.settings.learning_enabled and self.settings.learning_influence_prompts
        ):
            return ""
        try:
            from backend.services.learning_pipeline import get_learning_pipeline

            pipeline = get_learning_pipeline(self.settings)
            return pipeline.directive_block(
                self._learning_repository_id, getattr(plan, "bug_category", "") or ""
            )
        except Exception:  # noqa: BLE001 — context is advisory
            return ""

    @property
    def _learning_repository_id(self) -> str:
        return getattr(self, "_repository_id_cache", "")

    async def _load_intelligence(self, state: RunStateModel):
        if not (
            self.settings.repository_intelligence_enabled
            and self.settings.repair_memory_enabled
        ):
            return None
        try:
            return await load_repository_intelligence(self.store, state.run_id, self.settings)
        except Exception:  # noqa: BLE001 — patch generation never depends on this
            return None

    def _repair_matches(
        self,
        intelligence,
        plan: PatchPlan,
        original: str,
        state: RunStateModel,
    ) -> list[dict]:
        """Historical repairs resembling this one, as prompt-free metadata.

        Deliberately not fed into the prompt. A past patch shown to the model
        competes with the runtime evidence for this failure, and the evidence
        must always win — so this travels in the metrics payload and the proof
        trail only, where a human can see what the pipeline had seen before.
        """
        if intelligence is None or not intelligence.repair_memory.records:
            return []

        file_path = plan.target_file or plan.file
        query = RepairQuery(
            repository_hash=intelligence.repository_hash,
            file=file_path,
            file_hash=content_hash(original),
            function=plan.target_function,
            function_hash="",
            bug_type=classify_bug_type(
                state.reproduction,
                state.root_cause,
                (state.static_report or {}).get("prioritized"),
            ),
        )
        matches = find_similar_repairs(
            intelligence.repair_memory,
            query,
            limit=self.settings.repair_memory_max_matches,
        )
        return summarize_matches(matches)

    def _parse_root_cause(self, data: dict) -> RootCauseBrief:
        return RootCauseBrief.model_validate(data) if data else RootCauseBrief()

    def _parse_blast_graph(self, data: dict) -> BlastGraphResult:
        return BlastGraphResult.model_validate(data) if data else BlastGraphResult()

    def _parse_retry_brief(self, data: dict | None) -> RetryBrief | None:
        if not data:
            return None
        return RetryBrief.model_validate(data)

    def _resolve_scope_files(self, blast: BlastGraphResult, state: RunStateModel) -> list[str]:
        if blast.auto_patch_scope:
            return blast.auto_patch_scope
        static = state.static_report or {}
        findings = static.get("prioritized", [])
        return [f["file"] for f in findings[:1]]

    @staticmethod
    def _order_scope_files(scope_files: list[str], fix_dag: dict) -> list[str]:
        """Apply A6's issue order to the files A7 will actually patch."""
        nodes = {n.get("issue_id"): n for n in (fix_dag.get("nodes") or [])}
        ordered: list[str] = []
        for issue_id in fix_dag.get("execution_order") or []:
            for file_path in (nodes.get(issue_id) or {}).get("files") or []:
                if file_path in scope_files and file_path not in ordered:
                    ordered.append(file_path)
        ordered.extend(file_path for file_path in scope_files if file_path not in ordered)
        return ordered

    def _load_file_sources(
        self,
        repo: Path,
        scope_files: list[str],
        state: RunStateModel,
    ) -> dict[str, str]:
        sources: dict[str, str] = {}
        for file_path in scope_files:
            baseline = self._resolve_original_baseline(repo / file_path, file_path, state)
            if (repo / file_path).exists():
                sources[file_path] = baseline
        return sources

    def _resolve_original_baseline(
        self,
        full: Path,
        file_path: str,
        state: RunStateModel,
    ) -> str:
        if state.patch_bundle:
            for patch in state.patch_bundle.get("patches", []):
                if patch.get("file") == file_path and patch.get("original"):
                    return patch["original"]
        if full.exists():
            return full.read_text(encoding="utf-8")
        return ""

    def _previous_patch_for_file(self, state: RunStateModel, file_path: str) -> dict | None:
        if not state.patch_bundle:
            return None
        for patch in state.patch_bundle.get("patches", []):
            if patch.get("file") == file_path:
                return patch
        return None

    async def _generate_from_plan(
        self,
        plan: PatchPlan,
        original: str,
        style_exemplar: str,
        retry_brief: RetryBrief | None,
        mutation_result: dict | None,
        previous_patch: dict | None,
        repo: Path,
        retry_count: int,
        context_package: ContextPackage | None = None,
        run_id: str = "",
    ) -> tuple[PatchLLMOutput | None, dict]:
        retry_number = retry_count
        metrics = {
            # The file this attempt actually patched — `target_file` below is
            # `blast.origins[0]` on every plan in the same run (see
            # `enrich_patch_plan_from_runtime`), so it cannot be used to zip a
            # metrics entry back to its `PatchCandidate` when a run touches more
            # than one file. This can.
            "file": plan.file,
            "target_file": plan.target_file or plan.file,
            "target_function": plan.target_function,
            "runtime_prompt": uses_runtime_prompt(plan),
            "semantic_diff": False,
            "retry_reason": None,
            "retry_number": retry_number,
        }

        if retry_brief and retry_count >= 1:
            metrics["retry_reason"] = retry_reason_from_brief(retry_brief)

        if self.settings.stub_mode or not self.settings.llm_configured():
            output = apply_stub_plan(plan, original)
            metrics["semantic_diff"] = has_semantic_diff(original, output.patched_content)
            metrics["generation_source"] = "stub"
            return output, metrics

        llm = LLMService(
            self.settings, run_id=run_id, agent_id=self.agent_id, retry_count=retry_count
        )
        repo_context = str(repo)
        relevant_code, context_source = self._focus_section(plan, original, context_package)
        metrics["generation_source"] = "llm"
        metrics["context_source"] = context_source
        metrics["focus_chars"] = len(relevant_code)

        if uses_runtime_prompt(plan):
            prompt = build_runtime_patch_prompt(
                plan, relevant_code, style_exemplar, repo_context, complete_original=original
            )
        else:
            prompt = build_llm_prompt(plan, original, style_exemplar, repo_context)

        # Learned conventions, appended as context. Deliberately last and
        # deliberately additive: the prompt above is unchanged, and the block
        # states in its own text that runtime evidence outranks it.
        learned = self._learned_conventions(plan)
        if learned:
            prompt += f"\n\n{learned}"
            metrics["learning_directives"] = learned.count("\n- ")

        if retry_brief and retry_count >= 1:
            prompt += build_retry_prompt_section(
                retry_brief,
                mutation_result,
                previous_patch,
                retry_count,
            )

        system = (
            "You are a security-focused code repair assistant fixing a reproduced runtime bug. "
            "Apply the minimum code change required. Return valid Python only. "
            "Do not reformat unrelated code. Return the complete file."
        )

        try:
            output = await self._call_llm_with_integrity_guard(llm, prompt, system, original, metrics)
            if output is None:
                if not metrics.get("retry_reason"):
                    metrics["retry_reason"] = "integrity_failed"
                return None, metrics
            metrics["semantic_diff"] = True
            return output, metrics
        except Exception as exc:  # noqa: BLE001 — a failed call is a failed attempt
            # Returning `apply_stub_plan(...)` here handed back the *original
            # file* as though the model had produced it. The integrity gate then
            # rejected it as `no_op` and overwrote `retry_reason` with that —
            # so a call that never completed was recorded as "the model returned
            # an unchanged file", and the real cause vanished from the metrics.
            metrics["retry_reason"] = "llm_error"
            metrics["llm_error"] = str(exc)[:200]
            return None, metrics

    def _focus_section(
        self,
        plan: PatchPlan,
        original: str,
        context_package: ContextPackage | None,
    ) -> tuple[str, str]:
        """Choose the 'relevant code' block the model reasons over.

        The A5.5 focused context replaces only this section. The complete
        original file is still passed separately as `complete_original`, so the
        reconstruction contract and the integrity guard are unchanged: the model
        is still required to return every definition the original contained.
        """
        if (
            context_package is not None
            and context_package.prefer_focused
            and context_package.focused_context
            and context_package.target_file == plan.file
            and context_package.privacy_guard_status != "failed"
        ):
            return context_package.focused_context, "a5_5_context_package"

        return extract_relevant_code(original, plan.target_function), "a7_local_extraction"

    async def _call_llm_with_integrity_guard(
        self,
        llm: LLMService,
        prompt: str,
        system: str,
        original: str,
        metrics: dict,
    ) -> PatchLLMOutput | None:
        preserved_reason = metrics.get("retry_reason")
        output = await llm.structured(prompt, PatchLLMOutput, system=system)
        ok, reason = validate_patch_integrity(original, output.patched_content)
        if ok:
            metrics["retry_reason"] = preserved_reason
            return output

        retry_lines = []
        if reason == "no_op":
            retry_lines.append(
                "No semantic changes were generated.\n"
                "Produce an actual code modification that fixes the root cause."
            )
        retry_lines.append(COMPLETE_FILE_RETRY_INSTRUCTION)

        retry_prompt = f"{prompt}\n\n" + "\n".join(retry_lines)
        output = await llm.structured(retry_prompt, PatchLLMOutput, system=system)
        ok, reason = validate_patch_integrity(original, output.patched_content)
        if ok:
            metrics["retry_reason"] = preserved_reason
            return output
        metrics["retry_reason"] = reason or "integrity_failed"
        return None

    def _validate_python(self, code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False


def _learning_repository_id(state: RunStateModel) -> str:
    """Repository identity shared with the Phase 3 index and the learning layer."""
    try:
        from backend.services.repair_memory import repository_id

        return repository_id(state.repo_path or state.repo_clone_path or "")
    except Exception:  # noqa: BLE001
        return ""
