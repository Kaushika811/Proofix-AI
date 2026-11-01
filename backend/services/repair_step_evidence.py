"""Join repair candidates to recorded writes; never infer validation from file overlap."""
import hashlib
import json


def patch_fingerprint(bundle: dict) -> str:
    return hashlib.sha256(json.dumps(bundle.get("patches") or [], sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def step_evidence(state, node: dict) -> dict:
    bundle = state.patch_bundle or {}
    record = bundle.get("application_evidence") or {}
    current = bool(record) and record.get("patch_hash") == patch_fingerprint(bundle)
    files = list(node.get("files") or [])
    writes = (record.get("files") or {}) if current else {}
    matched = [f for f in files if f in writes]
    execution = "applied" if files and len(matched) == len(files) else "partially_applied" if matched else "unavailable"
    result = state.mutation_result or {}
    validated = bool(matched) and result.get("validated_patch_hash") == record.get("patch_hash") and current
    status = "unavailable"
    if validated and result.get("pytest_available") is True:
        if result.get("pytest_passed") is True:
            status = "passed"
        elif result.get("pytest_passed") is False:
            status = "failed"
    return {
        "execution": execution,
        "appliedFiles": matched,
        "changes": [{"file": f, **writes[f]} for f in matched],
        "validation": status,
        "validationScope": "patch_bundle" if validated else None,
        "targetTestPassed": result.get("target_test_passed") if validated else None,
        "regressionTestsPassed": result.get("regression_tests_passed") if validated else None,
        "patchHash": record.get("patch_hash") if current and matched else None,
        "note": "Applied means a write was recorded for the candidate's files. Validation covers the whole patch bundle, not independent proof of this candidate's correctness.",
    }
