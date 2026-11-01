"""One authority for whether a selected issue has a safe patch target."""

from backend.state.schema import RunStateModel


_DIRECT_SOURCES = frozenset({"issue_contract", "stack_trace", "import_mapping"})


def resolved_issue_target(state: RunStateModel) -> str | None:
    issue = state.issue_contract or {}
    reproduction = state.reproduction or {}
    blast = state.blast_graph or {}
    resolution = blast.get("target_resolution") or {}
    path = resolution.get("resolved_path")

    if not issue or reproduction.get("status") != "CONFIRMED":
        return None
    if path == (state.generated_test or {}).get("path"):
        return None
    if reproduction.get("failing_test") != issue.get("target_test"):
        return None
    if not path or resolution.get("source") not in _DIRECT_SOURCES:
        return None
    if path not in (blast.get("auto_patch_scope") or []):
        return None
    if issue.get("target_file") and path != issue["target_file"]:
        return None
    return str(path)


def issue_repair_scope(state: RunStateModel) -> list[str]:
    """Primary target plus source-located investigation files in safe scope.

    Blast reachability alone is not a request to change every dependent file.
    Supporting files must also be named by the investigation; A7 further
    intersects this set with A6's actual plan.
    """
    from pathlib import PurePosixPath

    target = resolved_issue_target(state)
    if not target:
        return []
    protected = {
        (state.generated_test or {}).get("path"),
        (state.issue_contract or {}).get("target_test", "").split("::")[0],
    }
    located = {
        c.get("file") for c in (state.root_cause or {}).get("citations", [])
        if (c.get("source_located") or c.get("verified"))
        and c.get("claim_support_status") != "contradicted"
    }
    supporting = []
    for path in (state.blast_graph or {}).get("auto_patch_scope", []):
        p = PurePosixPath(path)
        if (path in located and path != target and path not in protected
                and not p.is_absolute() and ".." not in p.parts
                and not {"tests", "test"}.intersection(p.parts)
                and not p.name.startswith("test_") and not p.name.endswith("_test.py")):
            supporting.append(path)
    return [target, *dict.fromkeys(supporting)]
