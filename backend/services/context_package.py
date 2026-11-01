"""Assemble the minimal repair context A7 consumes.

Takes ranked files, extracts the code each contributes, masks secrets, enforces a
character budget, and renders the focused block. The budget degrades in a fixed
order so the target function and the acceptance criteria are never the thing that
gets dropped.

The builder refuses to make things worse. `extract_relevant_code` — the path A5.5
replaces — is already minimal when it resolves a target function: imports plus
that one function. A5.5 cannot undercut it there, because its core additionally
carries per-file scaffolding and the module constants the target reads. So the
package declares, via `prefer_focused`, whether A7 should actually use it: only
when it is smaller than that baseline, or when it masked a secret the baseline
would have sent in clear. Otherwise A7 keeps its existing path and the metrics
report a reduction of zero rather than an invented saving.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.models.context import (
    ContextMetrics,
    ContextPackage,
    ExtractedSymbol,
    RankedContextFile,
    Redaction,
)
from backend.services.context_extractor import (
    FileExtraction,
    extract_from_file,
    render_focused_context,
)
from backend.services.context_ranker import RANKING_VERSION
from backend.services.privacy_guard import scan_source, scan_text
from backend.services.runtime_patch_prompt import extract_relevant_code

# Default character budget for the focused block.
#
# Deliberately set to the cap `runtime_patch_prompt.extract_relevant_code`
# already applies, so A5.5 can never send more than the path it replaces. That
# baseline is a blind character truncation of "imports + target function", which
# on a large file can cut the target off mid-body; A5.5 spends the same budget on
# whole, evidence-ordered symbols instead.
DEFAULT_BUDGET_CHARS = 4000

# Files beyond the target that may contribute context.
DEFAULT_SUPPORTING_FILES = 2

# Same estimator the LLM gateway uses, so reduction numbers are comparable.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(0, len(text or "") // CHARS_PER_TOKEN)


@dataclass
class PackageInputs:
    """Plain-data inputs. No RunState dependency, so this is unit-testable."""

    repo_path: Path
    target_file: str
    target_function: str | None = None
    ranked_files: list[RankedContextFile] = field(default_factory=list)
    root_cause_summary: str = ""
    runtime_evidence: dict = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    validation_requirements: list[str] = field(default_factory=list)
    patch_constraints: list[str] = field(default_factory=list)
    evidence_symbols: tuple[str, ...] = ()
    budget_chars: int = DEFAULT_BUDGET_CHARS
    supporting_files: int = DEFAULT_SUPPORTING_FILES
    # Cap the budget at the size of the section A5.5 replaces, guaranteeing the
    # layer can never increase prompt size. Disable only to measure the
    # unconstrained context A5.5 would prefer to send.
    respect_baseline_budget: bool = True
    cache_key: str = ""


EXPECTED_OUTPUT_FORMAT = (
    "Return the COMPLETE patched file. Every import, class, and function present "
    "in the original must remain. Do not abbreviate with placeholders such as "
    '"...", "# unchanged", or "# rest of file". The focused context above is for '
    "reasoning; the complete original file is the base you must reproduce with "
    "your fix applied."
)


def _scan_strings(values: list[str], field: str) -> tuple[list[str], list[Redaction]]:
    """Mask every string in a prompt-bound list, keeping what was masked.

    `acceptance_criteria`, `contracts`, `validation_requirements` and
    `patch_constraints` all reach the patch prompt and none of them were
    scanned — a JWT reached `acceptance_criteria[2]` with the guard reporting
    `clean` and zero redactions (B-B04). They are derived from exception
    messages and failing-test names, which is exactly where a leaked credential
    shows up.

    `field` stands in for a filename in the ledger: these strings have no source
    file, and recording a redaction with an empty path would make the audit
    unreadable at the point it matters most.
    """
    cleaned: list[str] = []
    redactions: list[Redaction] = []
    for index, value in enumerate(values or []):
        if not isinstance(value, str):
            cleaned.append(value)
            continue
        result = scan_text(value, f"{field}[{index}]")
        cleaned.append(result.source)
        redactions.extend(result.redactions)
    return cleaned, redactions


def _sanitize_extraction(extraction: FileExtraction) -> tuple[FileExtraction, list[Redaction]]:
    """Mask secrets in every extracted span before it can leave the process."""
    redactions: list[Redaction] = []

    def clean(symbols: list[ExtractedSymbol]) -> list[ExtractedSymbol]:
        out: list[ExtractedSymbol] = []
        for symbol in symbols:
            result = scan_source(symbol.source, extraction.file)
            redactions.extend(result.redactions)
            out.append(symbol.model_copy(update={"source": result.source}))
        return out

    cleaned_imports: list[str] = []
    for statement in extraction.imports:
        result = scan_source(statement, extraction.file)
        redactions.extend(result.redactions)
        cleaned_imports.append(result.source.rstrip("\n"))

    sanitized = FileExtraction(
        file=extraction.file,
        source=extraction.source,
        imports=cleaned_imports,
        functions=clean(extraction.functions),
        classes=clean(extraction.classes),
        constants=clean(extraction.constants),
        target=extraction.target,
        parsed=extraction.parsed,
        degraded=extraction.degraded,
        degraded_reason=extraction.degraded_reason,
    )
    return sanitized, redactions


def _clone(extraction: FileExtraction) -> FileExtraction:
    return FileExtraction(
        file=extraction.file,
        source=extraction.source,
        imports=list(extraction.imports),
        functions=list(extraction.functions),
        classes=list(extraction.classes),
        constants=list(extraction.constants),
        target=extraction.target,
        parsed=extraction.parsed,
        degraded=extraction.degraded,
        degraded_reason=extraction.degraded_reason,
    )


def _signature_form(symbol: ExtractedSymbol) -> ExtractedSymbol:
    """Collapse a symbol to its first line plus an elision marker."""
    if symbol.signature_only:
        return symbol
    head = symbol.source.split("\n", 1)[0].rstrip()
    return symbol.model_copy(update={"source": f"{head}\n    ...", "signature_only": True})


def _apply_budget(extractions: list[FileExtraction], budget: int) -> tuple[list[FileExtraction], bool]:
    """Shrink context to fit, dropping the least valuable material first.

    Degradation order, least to most costly to lose:

    1. supporting files entirely
    2. caller signatures (they establish the contract, not the defect)
    3. non-target function bodies, collapsed to signatures
    4. non-target functions, dropped lowest-ranked first
    5. classes other than the one enclosing the target

    The target function, the imports and the module constants it reads are never
    reduced — they are the minimum a repair cannot be made without.
    """
    if budget <= 0:
        return extractions, False

    def size(items: list[FileExtraction]) -> int:
        return len(render_focused_context(items))

    if size(extractions) <= budget:
        return extractions, False

    working = [_clone(e) for e in extractions]

    # 1. Supporting files.
    working = working[:1]
    if size(working) <= budget:
        return working, True

    primary = working[0]

    # 2. Callers.
    primary.functions = [f for f in primary.functions if f.kind != "caller_function"]
    if size(working) <= budget:
        return working, True

    # 3. Collapse non-target bodies.
    primary.functions = [
        f if f.kind == "target_function" else _signature_form(f) for f in primary.functions
    ]
    if size(working) <= budget:
        return working, True

    # 4. Drop helpers, furthest-from-target first (they are appended in
    #    increasing call distance, so the tail is the weakest).
    while size(working) > budget and len(primary.functions) > 1:
        primary.functions.pop()
    if size(working) <= budget:
        return working, True

    # 5. Classes that do not enclose the target.
    primary.classes = [c for c in primary.classes if c.reason == "encloses the target"]

    return working, True


def _core_only(extractions: list[FileExtraction]) -> list[FileExtraction]:
    """The irreducible context: target file, its target function, imports, constants."""
    if not extractions:
        return []
    primary = _clone(extractions[0])
    primary.functions = [f for f in primary.functions if f.kind == "target_function"]
    primary.classes = [c for c in primary.classes if c.reason == "encloses the target"]
    return [primary]


def build_package(inputs: PackageInputs) -> ContextPackage:
    """Rank → extract → sanitize → budget → render."""
    started = time.monotonic()
    metrics = ContextMetrics(budget_chars=inputs.budget_chars)

    ordered = list(inputs.ranked_files)
    metrics.files_ranked = len(ordered)

    # Target file first, then the highest-ranked supporting files.
    file_order = [inputs.target_file] if inputs.target_file else []
    for ranked in ordered:
        if ranked.file not in file_order:
            file_order.append(ranked.file)
    file_order = file_order[: 1 + max(0, inputs.supporting_files)]

    t_extract = time.monotonic()
    extractions: list[FileExtraction] = []
    for index, file in enumerate(file_order):
        extraction = extract_from_file(
            inputs.repo_path,
            file,
            target_function=inputs.target_function if index == 0 else None,
            extra_symbols=inputs.evidence_symbols if index == 0 else (),
            include_callers=index == 0,
        )
        if extraction.symbols or extraction.imports:
            extractions.append(extraction)
    metrics.extraction_time_ms = int((time.monotonic() - t_extract) * 1000)

    t_privacy = time.monotonic()
    redactions: list[Redaction] = []
    sanitized: list[FileExtraction] = []
    guard_status = "clean"
    try:
        for extraction in extractions:
            cleaned, found = _sanitize_extraction(extraction)
            sanitized.append(cleaned)
            redactions.extend(found)
        if redactions:
            guard_status = "masked"
    except Exception:  # noqa: BLE001 — guard must fail closed, never silently pass
        guard_status = "failed"
        sanitized = []
    metrics.privacy_time_ms = int((time.monotonic() - t_privacy) * 1000)

    original_source = _read(inputs.repo_path, inputs.target_file)

    # What A7 would send for this section without A5.5. Used both as the
    # reduction baseline and as the non-regression test below.
    baseline_focus = extract_relevant_code(original_source, inputs.target_function)

    effective_budget = inputs.budget_chars
    metrics.budget_chars = effective_budget

    sanitized, degraded_by_budget = _apply_budget(sanitized, effective_budget)
    focused = render_focused_context(sanitized)

    # Hard ceiling. Structural degradation cannot shrink a target function that
    # is itself larger than the budget, and the path A5.5 replaces applies a
    # blunt character cap at exactly this size. Enforcing the same ceiling makes
    # "A5.5 never sends more than the old path" true by construction rather than
    # by hope — within it, the budget is spent target-first instead of from the
    # top of the file.
    # The core — imports, referenced constants, the enclosing class shell and the
    # target function itself — is the floor. Truncating into it would hand the
    # model a half a function to repair, which is strictly worse than the blunt
    # baseline it replaces, so the ceiling is raised to fit the core when needed.
    core_floor = len(render_focused_context(_core_only(sanitized)))
    ceiling = max(effective_budget, core_floor) if effective_budget > 0 else 0

    if ceiling > 0 and len(focused) > ceiling:  # noqa: SIM102 — explicit for clarity
        suffix = "\n# ...truncated\n```"
        keep = max(0, ceiling - len(suffix))
        focused = focused[:keep].rstrip() + suffix
        degraded_by_budget = True

    # Refuse to claim a saving that does not exist. When extraction did not
    # shrink anything — a small file whose call graph is the whole file — fall
    # back to the *masked* complete source rather than to nothing: it yields no
    # token reduction, but it keeps the focus section sanitized, which dropping
    # to an empty package would silently give up.
    no_gain = bool(original_source) and len(focused) >= len(original_source)
    if guard_status == "failed":
        focused = ""
        metrics.degraded = True
    elif no_gain or not focused.strip():
        masked_full = scan_source(original_source, inputs.target_file)
        redactions.extend(masked_full.redactions)
        if masked_full.redactions:
            guard_status = "masked"
        focused = masked_full.source
        metrics.degraded = True

    # Every remaining string that reaches the prompt. The guard covered
    # extracted code and nothing else, so these four lists — all derived from
    # exception messages and test names, which is precisely where a leaked
    # credential surfaces — travelled unscanned (B-B04).
    criteria, criteria_found = _scan_strings(list(inputs.acceptance_criteria), "acceptance_criteria")
    contracts, contract_found = _scan_strings(list(inputs.contracts), "contracts")
    requirements, requirement_found = _scan_strings(
        list(inputs.validation_requirements), "validation_requirements"
    )
    constraints, constraint_found = _scan_strings(
        list(inputs.patch_constraints), "patch_constraints"
    )
    summary_result = scan_text(inputs.root_cause_summary, "root_cause_summary")
    evidence, evidence_found = _sanitize_evidence(inputs.runtime_evidence, inputs.repo_path)

    redactions.extend(criteria_found)
    redactions.extend(contract_found)
    redactions.extend(requirement_found)
    redactions.extend(constraint_found)
    redactions.extend(summary_result.redactions)
    redactions.extend(evidence_found)

    # The status is recomputed from the full ledger. Setting it only inside the
    # extraction loop meant a secret found anywhere else was masked and then
    # reported as `clean`. `failed` still wins: a guard that errored knows
    # nothing about what it did or did not see.
    if guard_status != "failed" and redactions:
        guard_status = "masked"

    primary = sanitized[0] if sanitized else None
    package = ContextPackage(
        ranking_version=RANKING_VERSION,
        cache_key=inputs.cache_key,
        root_cause_summary=summary_result.source,
        runtime_evidence=evidence,
        target_file=inputs.target_file,
        target_function=inputs.target_function,
        acceptance_criteria=criteria,
        relevant_imports=list(primary.imports) if primary else [],
        relevant_classes=list(primary.classes) if primary else [],
        relevant_functions=list(primary.functions) if primary else [],
        related_utilities=[s for e in sanitized[1:] for s in e.symbols],
        constants=list(primary.constants) if primary else [],
        dependency_summary=_dependency_summary(ordered),
        contracts=contracts,
        validation_requirements=requirements,
        patch_constraints=constraints,
        expected_output_format=EXPECTED_OUTPUT_FORMAT,
        focused_context=focused,
        original_complete_file=original_source,
        ranked_files=ordered,
        redactions=redactions,
        privacy_guard_status=guard_status,
    )

    metrics.files_extracted = len(sanitized)
    metrics.context_files = len({e.file for e in sanitized})
    metrics.context_functions = sum(len(e.functions) for e in sanitized)
    metrics.context_lines = focused.count("\n") if focused else 0
    metrics.privacy_redactions = len(redactions)
    metrics.degraded = metrics.degraded or degraded_by_budget or (primary.degraded if primary else True)

    # Adoption decision.
    #
    # `extract_relevant_code` is already minimal when it resolves a target: it
    # emits imports plus that one function. A5.5 cannot undercut it there — its
    # core carries per-file scaffolding and the module constants the baseline
    # omits. So the package is only adopted when it actually earns its place:
    # it is smaller than the baseline, or it masked a secret the baseline would
    # have sent in clear. Otherwise A7 keeps its existing path unchanged and the
    # layer reports a reduction of zero rather than a regression.
    smaller_than_baseline = len(focused) <= len(baseline_focus)
    masked_a_leak = bool(redactions)
    package.prefer_focused = bool(focused) and (smaller_than_baseline or masked_a_leak)

    if not focused:
        metrics.adoption_reason = "no context could be extracted for this file"
    elif smaller_than_baseline and masked_a_leak:
        metrics.adoption_reason = (
            "smaller than A7's own baseline extraction and masked a secret the baseline "
            "would have sent unredacted"
        )
    elif smaller_than_baseline:
        metrics.adoption_reason = "smaller than A7's own baseline extraction"
    elif masked_a_leak:
        metrics.adoption_reason = (
            "masked a secret A7's own baseline extraction would have sent unredacted"
        )
    else:
        metrics.adoption_reason = (
            "no smaller than A7's own baseline extraction, and found nothing to mask"
        )

    metrics.original_tokens = estimate_tokens(baseline_focus)
    metrics.reduced_tokens = (
        estimate_tokens(focused) if package.prefer_focused else metrics.original_tokens
    )
    metrics.estimated_saved_tokens = max(0, metrics.original_tokens - metrics.reduced_tokens)
    metrics.token_reduction = (
        round(metrics.estimated_saved_tokens / metrics.original_tokens, 4)
        if metrics.original_tokens
        else 0.0
    )
    metrics.estimated_prompt_tokens = metrics.reduced_tokens + estimate_tokens(original_source)
    metrics.build_time_ms = int((time.monotonic() - started) * 1000)

    package.metrics = metrics
    return package


def _read(repo_path: Path, file: str) -> str:
    if not file:
        return ""
    try:
        return (repo_path / file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _sanitize_evidence(evidence: dict, repo_path: Path | None = None) -> tuple[dict, list[Redaction]]:
    """Mask credentials that leaked into tracebacks and exception messages.

    Now returns what it masked. It used to drop the redactions on the floor, so
    a secret found in a traceback was correctly removed from the prompt and then
    reported as `privacy_guard_status: "clean"` with an empty ledger — the
    masking happened and the audit denied it. An unrecorded redaction is
    indistinguishable from no secret, which is the one thing this ledger exists
    to tell apart.
    """
    cleaned: dict = {}
    redactions: list[Redaction] = []
    for key, value in (evidence or {}).items():
        if not isinstance(value, str):
            cleaned[key] = value
            continue
        if repo_path is not None:
            value = value.replace(str(repo_path.resolve()) + "/", "")
        result = scan_text(value, f"runtime_evidence.{key}")
        cleaned[key] = result.source
        redactions.extend(result.redactions)
    return cleaned, redactions


def _dependency_summary(ranked: list[RankedContextFile]) -> list[str]:
    return [
        f"{f.file} (score {f.score:.2f}, confidence {f.confidence:.2f}): {f.reason}"
        for f in ranked[:8]
    ]
