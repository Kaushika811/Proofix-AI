"""Runtime-evidence-driven patch prompt construction for A7."""

from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path

from backend.models.blast import BlastGraphResult
from backend.models.patch import PatchPlan
from backend.models.root_cause import RootCauseBrief
from backend.models.validation import RetryBrief

FUNCTION_CALL_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(")
STACK_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+)')


def infer_target_function(
    plan: PatchPlan,
    root_cause: RootCauseBrief,
    reproduction: dict | None,
    source: str,
    repo_path: Path | None = None,
) -> str | None:
    texts = [
        root_cause.root_cause or "",
        root_cause.summary or "",
        plan.required_behavior_change or "",
    ]
    for text in texts:
        match = re.search(r"\b([a-z_][a-z0-9_]*)\s*\(\)", text)
        if match:
            name = match.group(1)
            if not source or _function_exists(source, name):
                return name

    for citation in root_cause.citations:
        if citation.file and (citation.file in plan.file or plan.file.endswith(citation.file)):
            fn = _function_at_line(source, citation.line)
            if fn:
                return fn

    stack = (reproduction or {}).get("traceback") or (reproduction or {}).get("stack_trace") or ""
    for frame_path, line_str in STACK_FRAME_RE.findall(stack):
        if plan.file in frame_path.replace("\\", "/"):
            fn = _function_at_line(source, int(line_str))
            if fn:
                return fn

    failing_test = (reproduction or {}).get("failing_test") or ""
    if failing_test and reproduction and repo_path:
        fn = _function_from_failing_test(reproduction, plan.file, repo_path)
        if fn:
            return fn

    return None


def derive_runtime_behaviors(
    root_cause: RootCauseBrief,
    reproduction: dict | None,
) -> tuple[str, str, str]:
    repro = reproduction or {}
    failing_test = repro.get("failing_test") or ""
    exception_type = repro.get("exception_type") or "AssertionError"
    exception_message = repro.get("exception_message") or ""

    current = root_cause.root_cause or root_cause.summary or "Buggy behavior reproduced by failing test."
    if repro.get("status") == "CONFIRMED":
        current = (
            f"Runtime failure confirmed ({exception_type}): {exception_message}. "
            f"{current}"
        ).strip()

    expected = _expected_from_evidence(failing_test, root_cause, exception_type, exception_message)

    acceptance = f"pytest {failing_test} passes" if failing_test else "All targeted tests pass after the fix."
    return current[:800], expected[:800], acceptance


def _expected_from_evidence(
    failing_test: str,
    root_cause: RootCauseBrief,
    exception_type: str,
    exception_message: str,
) -> str:
    """What the patch must achieve, stated only from evidence this run produced.

    This replaces a keyword guess that inferred *domain semantics* from a test
    name. It returned "Reject tokens whose exp timestamp is earlier than
    time.time()" whenever the root-cause text contained the substring `exp` —
    which matches "unexpected", "export", "explicit", "experiment". A run
    repairing a null dereference was handed JWT expiry as its fix target, in the
    one section of the prompt that tells the model what success looks like.

    A guess about what a repository *means* has no evidence behind it and cannot
    be verified, so there is nothing to state but what the pipeline actually
    observed: the test that fails, the exception it raises, and A4's
    citation-verified conclusion. If that is thin, the honest prompt is thin.
    """
    parts: list[str] = []

    if failing_test:
        parts.append(f"`{failing_test}` must pass.")

    # The exception is the failure in the repository's own words. Reported
    # without interpretation — naming the type is a fact, guessing why it was
    # raised is not.
    if exception_message:
        parts.append(f"The {exception_type} it currently raises ({exception_message}) must not occur.")
    elif exception_type:
        parts.append(f"The {exception_type} it currently raises must not occur.")

    conclusion = root_cause.root_cause or root_cause.summary
    if conclusion:
        parts.append(f"Address the root cause: {conclusion}")

    return " ".join(parts) or "The reproduced failure must no longer occur."


def enrich_patch_plan_from_runtime(
    plan: PatchPlan,
    root_cause: RootCauseBrief,
    reproduction: dict | None,
    blast: BlastGraphResult,
    source: str = "",
    repo_path: Path | None = None,
) -> PatchPlan:
    repro = reproduction or {}
    target_file = plan.file

    target_function = infer_target_function(plan, root_cause, repro, source, repo_path)

    current, expected, acceptance = derive_runtime_behaviors(root_cause, repro)
    stack = repro.get("traceback") or repro.get("stack_trace") or plan.stack_evidence or ""

    runtime_parts = []
    if repro.get("status"):
        runtime_parts.append(f"status={repro['status']}")
    if repro.get("failing_test"):
        runtime_parts.append(f"failing_test={repro['failing_test']}")
    if repro.get("exception_type"):
        runtime_parts.append(f"exception={repro['exception_type']}: {repro.get('exception_message', '')}")

    return plan.model_copy(
        update={
            "target_file": target_file,
            "target_function": target_function,
            "current_behavior": current,
            "expected_behavior": expected,
            "acceptance_criteria": acceptance,
            "runtime_evidence": "; ".join(runtime_parts)[:1000],
            "failing_test": repro.get("failing_test") or "",
            "stack_summary": stack[:500],
        }
    )


def extract_relevant_code(source: str, target_function: str | None, limit: int = 4000) -> str:
    if not target_function or not _function_exists(source, target_function):
        return source[:limit]

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source[:limit]

    import_lines: list[str] = []
    func_lines: list[str] = []

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            segment = ast.get_source_segment(source, node)
            if segment:
                import_lines.append(segment if segment.endswith("\n") else segment + "\n")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_function:
            segment = ast.get_source_segment(source, node)
            if segment:
                func_lines.append(segment if segment.endswith("\n") else segment + "\n")

    if not func_lines:
        return source[:limit]

    combined = "".join(import_lines) + "\n" + "".join(func_lines)
    return combined[:limit]


def build_runtime_patch_prompt(
    plan: PatchPlan,
    source_for_prompt: str,
    style_exemplar: str,
    repo_context: str,
    complete_original: str = "",
) -> str:
    target_fn = plan.target_function or "(infer from root cause — modify minimum necessary code)"
    constraints = "\n".join(f"- {c}" for c in plan.security_constraints) or "- None"
    goals = "\n".join(f"- {g}" for g in plan.validation_goals) or "- Patch must resolve root cause without regressions"

    return f"""You are fixing an already reproduced bug.

## Repository Context
{repo_context or "Python application repository"}

## Target File
{plan.target_file}

## Target Function
{target_fn}

## Root Cause
{plan.root_cause}

## Current Behaviour
{plan.current_behavior or plan.required_behavior_change}

## Expected Behaviour
{plan.expected_behavior or plan.required_behavior_change}

## Acceptance Criteria
{plan.acceptance_criteria or "Targeted tests pass after the fix."}

## Runtime Evidence
{plan.runtime_evidence or plan.stack_summary or plan.stack_evidence[:500]}

## Stack Summary
{plan.stack_summary or plan.stack_evidence[:500]}

## Security constraints
{constraints}

## Validation goals
{goals}

## Relevant code (focus changes here)
{source_for_prompt[:4000]}

## Original complete file (return the ENTIRE file with your fix applied — no omissions)
{complete_original[:8000] if complete_original else source_for_prompt[:4000]}

## Style exemplar (recent git history for this file)
{style_exemplar[:1500]}

## Instructions
- Only modify {target_fn} unless absolutely required.
- Preserve all existing behaviour outside the fix.
- Do not edit unrelated functions.
- Do not reformat the file.
- Return the COMPLETE executable Python file — every import, function, and class from the original must remain.
- Never use placeholders such as "...", "# unchanged", "# omitted", or "# remainder of file".
- The fix must satisfy: {plan.acceptance_criteria or plan.validation_goals[0] if plan.validation_goals else "acceptance criteria above"}"""


def build_retry_prompt_section(
    retry_brief: RetryBrief,
    mutation_result: dict | None,
    previous_patch: dict | None,
    retry_number: int,
) -> str:
    if retry_number < 1:
        return ""

    sections = ["\n\n## Retry — previous attempt failed\n"]

    patch_summary = retry_brief.previous_patch_summary
    if not patch_summary and previous_patch:
        original = previous_patch.get("original") or ""
        patched = previous_patch.get("patched") or ""
        if original and patched:
            diff = difflib.unified_diff(
                original.splitlines(),
                patched.splitlines(),
                fromfile="previous",
                tofile="attempt",
                lineterm="",
            )
            diff_text = "\n".join(list(diff)[:40])
            patch_summary = f"```diff\n{diff_text}\n```"
    if patch_summary:
        sections.append(f"### Previous patch summary\n{patch_summary}\n")

    vf = retry_brief.validation_failure
    if vf or retry_brief.assertion_failure:
        sections.append("### Validation failure\n")
        if vf and vf.failing_test:
            sections.append(f"pytest `{vf.failing_test}` failed.\n")
        if retry_brief.expected_behaviour or (vf and vf.expected_value):
            expected = retry_brief.expected_behaviour or vf.expected_value
            sections.append(f"Expected: {expected}\n")
        if retry_brief.actual_behaviour or (vf and vf.actual_value):
            actual = retry_brief.actual_behaviour or vf.actual_value
            sections.append(f"Actual: {actual}\n")
        if retry_brief.assertion_failure:
            sections.append(f"Assertion: {retry_brief.assertion_failure}\n")
        elif vf and vf.assertion_message:
            sections.append(f"Assertion: {vf.assertion_message}\n")

    if retry_brief.violated_contract:
        sections.append(f"### Violated contract\n{retry_brief.violated_contract}\n")
    if retry_brief.security_constraint:
        sections.append(f"### Security constraint\n{retry_brief.security_constraint}\n")

    trace = retry_brief.stack_trace or (vf.traceback if vf else None)
    if trace:
        sections.append(f"### Traceback\n{trace[:800]}\n")

    mutation = mutation_result or {}
    if mutation.get("pytest_passed") is False and not vf:
        sections.append("### Pytest result\npytest FAILED after previous patch.\n")
    if mutation.get("mutant_survived"):
        sections.append(
            "### Mutation result\n"
            "A surviving mutant indicates the previous patch did not validate the fix.\n"
        )

    # The example that used to close this instruction — "(e.g. expiry
    # comparison if tokens must be rejected)" — described one fixture
    # repository's bug. On every other repository it named a change the code has
    # no reason to contain, in the sentence telling the model what to do next.
    instruction = retry_brief.retry_instruction or (
        "The previous patch did not fix the bug. Validation still fails.\n"
        "Generate a DIFFERENT implementation.\n"
        "Do not repeat the previous patch.\n"
        "Make the semantic change the failing test requires.\n"
    )
    sections.append(f"### Retry instruction\n{instruction}\n")
    return "".join(sections)


def is_no_op_patch(original: str, patched: str) -> bool:
    if original == patched:
        return True
    return original.strip() == patched.strip()


def has_semantic_diff(original: str, patched: str) -> bool:
    try:
        return ast.dump(ast.parse(original), include_attributes=False) != ast.dump(
            ast.parse(patched), include_attributes=False
        )
    except SyntaxError:
        return not is_no_op_patch(original, patched)


def changed_python_symbols(original: str, patched: str) -> set[str]:
    """Names of changed definitions, including nested functions and methods.

    Module-level statements and imports are reported as ``<module>``. This is
    a structural comparison, not a claim that a behavioral fix is correct.
    """
    def definitions(source: str) -> dict[str, str]:
        found: dict[str, str] = {}

        def visit(body: list[ast.stmt], prefix: str = "") -> None:
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = f"{prefix}.{node.name}" if prefix else node.name
                    own = ast.parse(ast.unparse(node))
                    definition = own.body[0]
                    if isinstance(definition, ast.ClassDef):
                        definition.body = [item for item in definition.body if not isinstance(
                            item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                        )]
                    found[name] = ast.dump(definition, include_attributes=False)
                    if isinstance(node, ast.ClassDef):
                        visit(node.body, name)

        tree = ast.parse(source)
        visit(tree.body)
        module = [node for node in tree.body if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        )]
        found["<module>"] = ast.dump(ast.Module(body=module, type_ignores=[]), include_attributes=False)
        return found

    before, after = definitions(original), definitions(patched)
    return {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}


ABBREVIATION_MARKERS = (
    "remainder of file unchanged",
    "remainder of file",
    "# unchanged",
    "# omitted",
    "... omitted",
    "unchanged (",
    "do not summarize",
)


def has_placeholder_text(patched: str) -> bool:
    lowered = patched.lower()
    if any(marker in lowered for marker in ABBREVIATION_MARKERS):
        return True
    for line in patched.splitlines():
        stripped = line.strip().lower()
        if stripped in ("...", "# ...", '"..."', "'...'"):
            return True
        if stripped.startswith("# ...") and "unchanged" in stripped:
            return True
    return False


def top_level_definitions(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def missing_top_level_definitions(original: str, patched: str) -> set[str]:
    return top_level_definitions(original) - top_level_definitions(patched)


def is_abbreviated_patch(original: str, patched: str) -> bool:
    if has_placeholder_text(patched):
        return True
    if missing_top_level_definitions(original, patched):
        return True
    return False


def validate_patch_integrity(
    original: str, patched: str, *, file: str = "", target_symbol: str | None = None,
) -> tuple[bool, str | None]:
    if is_no_op_patch(original, patched):
        return False, "no_op"
    try:
        ast.parse(patched)
    except SyntaxError:
        return False, "syntax_error"
    if not file or file.endswith(".py"):
        if not has_semantic_diff(original, patched):
            return False, "non_behavioral_change"
        if target_symbol:
            changed = changed_python_symbols(original, patched)
            if not any(name == target_symbol or name.endswith(f".{target_symbol}")
                       for name in changed):
                return False, "unrelated_target_symbol"
    if is_abbreviated_patch(original, patched):
        return False, "abbreviated"
    return True, None


COMPLETE_FILE_RETRY_INSTRUCTION = (
    "Return the COMPLETE executable Python file. "
    "Do not summarize unchanged code. "
    "Include every import, function, and class from the original file."
)


def uses_runtime_prompt(plan: PatchPlan) -> bool:
    return bool(plan.failing_test or "status=CONFIRMED" in plan.runtime_evidence)


def _function_exists(source: str, name: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return name in source
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        for node in ast.walk(tree)
    )


def _function_at_line(source: str, line: int | None) -> str | None:
    if not line:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= line <= (node.end_lineno or node.lineno) and (best is None or node.lineno > best[0]):
                best = (node.lineno, node.name)
    return best[1] if best else None


def _function_from_failing_test(reproduction: dict, target_file: str, repo_path: Path) -> str | None:
    failing_test = reproduction.get("failing_test") or ""
    if not failing_test:
        return None

    test_path = failing_test.split("::")[0]
    full = repo_path / test_path
    if not full.is_file():
        return None

    try:
        source = full.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None

    # A pytest node identifies one function, possibly inside a class and with
    # parametrization. Other tests in the module are not evidence for this bug.
    parts = failing_test.split("::")[1:]
    if not parts:
        return None
    body = tree.body
    selected = None
    for part in parts:
        name = part.split("[", 1)[0]
        selected = next((n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name == name), None)
        if selected is None:
            return None
        body = selected.body

    module = target_file.removesuffix(".py").replace("/", ".")
    if module.endswith(".__init__"):
        module = module.removesuffix(".__init__")
    def matches(value):
        return bool(value and (module == value or module.endswith("." + value)))

    candidates = set()
    visited = set()
    def inspect(node, context):
        key = (id(context), id(node))
        if key in visited:
            return
        visited.add(key)
        functions = {}
        modules = {}
        for item in [*context.body, *ast.walk(node)]:
            if isinstance(item, ast.ImportFrom):
                for alias in item.names:
                    if matches(item.module):
                        functions[alias.asname or alias.name] = alias.name
                    elif matches(".".join(filter(None, [item.module, alias.name]))):
                        modules[alias.asname or alias.name] = module
            elif isinstance(item, ast.Import):
                for alias in item.names:
                    if matches(alias.name):
                        modules[alias.asname or alias.name] = module
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Name) and call.func.id in functions:
                candidates.add(functions[call.func.id])
            elif isinstance(call.func, ast.Attribute):
                prefix = ast.unparse(call.func.value)
                if prefix in modules:
                    candidates.add(call.func.attr)
        # Inspect only fixtures used by this test, including local conftest
        # fixtures. Their definitions and aliases stay in their own module.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            requested = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
            for ctx in contexts:
                for fixture in ctx.body:
                    if not isinstance(fixture, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    fixture_name = fixture.name
                    is_fixture = False
                    for decorator in fixture.decorator_list:
                        dec = decorator.func if isinstance(decorator, ast.Call) else decorator
                        if ast.unparse(dec).split(".")[-1] == "fixture":
                            is_fixture = True
                            if isinstance(decorator, ast.Call):
                                for kw in decorator.keywords:
                                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                                        fixture_name = kw.value.value
                    if is_fixture and fixture_name in requested:
                        inspect(fixture, ctx)

    contexts = [tree]
    parent = full.parent
    while parent.is_relative_to(repo_path):
        conftest = parent / "conftest.py"
        if conftest.is_file():
            try:
                contexts.append(ast.parse(conftest.read_text(encoding="utf-8")))
            except (OSError, SyntaxError):
                pass
        if parent == repo_path:
            break
        parent = parent.parent
    inspect(selected, tree)
    try:
        target_source = (repo_path / target_file).read_text(encoding="utf-8")
    except OSError:
        return None
    candidates = {name for name in candidates if _function_exists(target_source, name)}
    return next(iter(candidates)) if len(candidates) == 1 else None
