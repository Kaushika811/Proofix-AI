"""Controlled, pinned-test comparisons for a proposed Python repair.

These are evidence about one test under three source states, not a proof of
semantic correctness across inputs or environments.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import shutil
import tempfile
from pathlib import Path

from backend.services.acceptance_test_integrity import acceptance_test_integrity_error
from backend.services.reproduction_parser import load_pytest_report
from backend.services.runtime_patch_prompt import changed_python_symbols, has_semantic_diff
from backend.services.subprocess_runner import PYTHON, run_command


def _hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def _assertion_headline(message: str) -> str:
    """Keep the observed assertion, excluding pytest's rendering diagnostics.

    Expanded operands (the `where` lines) and verbose diffs can include clocks,
    object addresses, or verbosity-dependent truncation. The first line retains
    the actual comparison; its file and line are checked separately.
    """
    first = (message.strip().splitlines() or [""])[0].strip()
    return first.removeprefix("AssertionError: ")


def _failure_identity(crash: dict, work: Path) -> dict:
    path = crash.get("path")
    if path and Path(path).is_absolute():
        try:
            path = str(Path(path).resolve().relative_to(work.resolve()))
        except ValueError:
            pass
    return {"path": path, "line": crash.get("lineno"),
            "assertion": _assertion_headline(crash.get("message") or "")}


def _counterfactual(original: str, patched: str, symbol: str) -> str | None:
    """Restore only the selected function, leaving other patched AST nodes."""
    before, after = ast.parse(original), ast.parse(patched)

    def locate(tree: ast.AST) -> tuple[list, int] | None:
        def scan(body: list, prefix: str = "") -> tuple[list, int] | None:
            for index, node in enumerate(body):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                name = f"{prefix}.{node.name}" if prefix else node.name
                if name == symbol and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return body, index
                if isinstance(node, ast.ClassDef):
                    nested = scan(node.body, name)
                    if nested:
                        return nested
            return None
        return scan(tree.body)

    old, new = locate(before), locate(after)
    if not old or not new:
        return None
    new[0][new[1]] = copy.deepcopy(old[0][old[1]])
    return ast.unparse(ast.fix_missing_locations(after)) + "\n"


def _test_calls_target(repo: Path, acceptance_test: dict, patch_file: str, symbol: str) -> bool:
    """Require a direct import/call link to the selected Python target."""
    try:
        source = (repo / acceptance_test["path"]).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (KeyError, OSError, SyntaxError):
        return False
    module = patch_file.removesuffix(".py").replace("/", ".")
    short = symbol.rsplit(".", 1)[-1]
    imported = {
        alias.asname or alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names if alias.name == short
    }
    module_aliases = {
        alias.asname or alias.name.split(".", 1)[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names if alias.name == module
    }
    return any(
        isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id in imported)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == short
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in module_aliases)
        ) for node in ast.walk(tree)
    )


async def evaluate_patch_causality(
    repo: Path, patches: list[dict], acceptance_test: dict | None,
    *, target_symbol: str | None = None, expected_failure: str | None = None,
    expected_failure_file: str | None = None, expected_failure_line: int | None = None,
    timeout: int = 120,
) -> dict:
    result = {
        "behavioral_change_detected": False,
        "patch_relevance_established": False,
        "causal_effect_supported": None,
        "acceptance_test_passed": None,
        "original": None, "patched": None, "counterfactual": None,
        "source_hashes": {}, "test_hash": (acceptance_test or {}).get("sha256"),
        "reason": None,
    }
    if not acceptance_test or not patches:
        result["reason"] = "Pinned acceptance test or patch is unavailable"
        return result
    error = acceptance_test_integrity_error(repo, acceptance_test)
    if error:
        result["reason"] = error
        return result
    nodeid = acceptance_test.get("nodeid")
    if not nodeid:
        result["reason"] = "Pinned acceptance test has no node ID"
        return result
    result["behavioral_change_detected"] = any(
        has_semantic_diff(p["original"], p["patched"]) if p["file"].endswith(".py")
        else p["original"] != p["patched"] for p in patches
    )
    related = []
    for patch in patches:
        if not patch["file"].endswith(".py"):
            continue
        changed = changed_python_symbols(patch["original"], patch["patched"])
        selected = target_symbol or patch.get("target_symbol")
        if not selected:
            selected = next((name for name in changed if name != "<module>" and
                             _test_calls_target(repo, acceptance_test, patch["file"], name)), None)
        if selected and any(name == selected or name.endswith(f".{selected}") for name in changed):
            if _test_calls_target(repo, acceptance_test, patch["file"], selected):
                related.append((patch, selected))
    result["patch_relevance_established"] = bool(related)
    result["source_hashes"] = {
        p["file"]: {"original": _hash(p["original"]), "patched": _hash(p["patched"])}
        for p in patches
    }
    if any(not (repo / p["file"]).is_file() or
           _hash((repo / p["file"]).read_text(encoding="utf-8")) != _hash(p["patched"])
           for p in patches):
        result["reason"] = "Patched checkout does not match the recorded patch bundle"
        return result

    ignore = shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache")
    with tempfile.TemporaryDirectory(prefix="proofix-causality-") as temporary:
        root = Path(temporary)

        async def run_state(label: str, overrides: dict[str, str]) -> dict:
            work = root / label
            shutil.copytree(repo, work, ignore=ignore, symlinks=True)
            for relative, source in overrides.items():
                path = (work / relative).resolve()
                if not path.is_relative_to(work.resolve()) or not path.is_file():
                    return {"outcome": "execution_error", "detail": "Patch file missing or outside repository"}
                path.write_text(source, encoding="utf-8")
            source_hashes = {
                p["file"]: _hash((work / p["file"]).read_text(encoding="utf-8"))
                for p in patches
            }
            if acceptance_test_integrity_error(work, acceptance_test):
                return {"outcome": "execution_error", "detail": "Acceptance test changed in comparison"}
            outcomes = []
            for number in (1, 2):
                report = work / f".proofix_causality_{number}.json"
                report.unlink(missing_ok=True)
                code, stdout, stderr = await run_command(
                    [PYTHON, "-m", "pytest", nodeid, "-v", "--tb=long", "--json-report",
                     f"--json-report-file={report}"], cwd=work, timeout=timeout,
                )
                if acceptance_test_integrity_error(work, acceptance_test):
                    return {"outcome": "execution_error", "detail": "Acceptance test changed during comparison"}
                if any(_hash((work / relative).read_text(encoding="utf-8")) != digest
                       for relative, digest in source_hashes.items()):
                    return {"outcome": "execution_error", "detail": "Source changed during comparison"}
                data = load_pytest_report(report) or {}
                selected = next((t for t in data.get("tests", []) if t.get("nodeid") == nodeid), {})
                call = selected.get("call") or {}
                crash = (call.get("crash") or {}).get("message", "")
                if code == 0 and selected.get("outcome") == "passed" and call.get("outcome") == "passed":
                    outcomes.append("passed")
                elif code == 1 and selected.get("outcome") == "failed" and (
                    crash.startswith(("AssertionError", "assert ", "Failed: DID NOT RAISE"))
                ):
                    outcomes.append(("behavioral_assertion_failed", crash,
                                     _failure_identity(call.get("crash") or {}, work)))
                else:
                    return {"outcome": "execution_error", "detail": (crash or stderr or stdout)[-400:]}
            identities = [o if isinstance(o, str) else (o[0], o[2]) for o in outcomes]
            if identities[0] != identities[1]:
                return {"outcome": "nondeterministic", "detail": outcomes}
            if isinstance(outcomes[0], tuple):
                return {"outcome": outcomes[0][0], "assertion": outcomes[0][1],
                        "failure_identity": outcomes[0][2]}
            return {"outcome": outcomes[0]}

        original_sources = {p["file"]: p["original"] for p in patches}
        result["original"] = await run_state("original", original_sources)
        result["patched"] = await run_state("patched", {})
        result["acceptance_test_passed"] = result["patched"]["outcome"] == "passed"
        if len(related) == 1:
            patch, selected_symbol = related[0]
            reverted = _counterfactual(patch["original"], patch["patched"], selected_symbol)
            if reverted:
                result["source_hashes"][patch["file"]]["counterfactual"] = _hash(reverted)
                result["counterfactual"] = await run_state("counterfactual", {patch["file"]: reverted})

    original_failed = result["original"]["outcome"] == "behavioral_assertion_failed"
    if original_failed and expected_failure:
        observed = result["original"].get("assertion") or ""
        identity = result["original"].get("failure_identity") or {}
        original_failed = (
            _assertion_headline(expected_failure) == _assertion_headline(observed)
            and (not expected_failure_file or identity.get("path") == expected_failure_file)
            and (expected_failure_line is None or identity.get("line") == expected_failure_line)
        )
        if not original_failed:
            result["reason"] = "Original failure differs from A3.5's recorded assertion"
    patched_passed = result["patched"]["outcome"] == "passed"
    counterfactual = result["counterfactual"]
    if original_failed and patched_passed and result["patch_relevance_established"] and counterfactual:
        identity = result["original"].get("failure_identity") or {}
        if (counterfactual["outcome"] == "behavioral_assertion_failed" and
                identity.get("path") and identity.get("line") is not None and
                counterfactual.get("failure_identity") == identity):
            result["causal_effect_supported"] = True
        elif counterfactual["outcome"] == "passed":
            result["causal_effect_supported"] = False
    if result["causal_effect_supported"] is None and not result["reason"]:
        result["reason"] = "Comparison or counterfactual did not establish patch-specific causality"
    return result
