"""`services/ui_projection.py::build_repair_plan` — A6's projection.

Every assertion traces to a field on `FixDAGPlan` (`models/fix_dag.py`) or a
read-only join against `state.static_report` / `state.cve_report`. The point
of this suite is to catch the projection inventing a confidence, silently
dropping a node the LLM ordering omitted, or losing the fact that A7 does not
actually execute this plan's order.
"""

from backend.services.ui_projection import build_repair_plan
from backend.state.schema import RunStateModel


def _state(**overrides) -> RunStateModel:
    base = dict(run_id="r", repo_path="/repo")
    base.update(overrides)
    return RunStateModel(**base)


def _dag(**overrides) -> dict:
    base = {
        "nodes": [
            {"issue_id": "cve-CVE-1", "files": ["app/auth.py"], "depends_on": []},
            {"issue_id": "finding-0", "files": ["app/auth.py"], "depends_on": ["cve-CVE-1"]},
        ],
        "execution_order": ["cve-CVE-1", "finding-0"],
        "conflict_batches": [],
        "dependency_edges": [
            {"from_issue": "cve-CVE-1", "to_issue": "finding-0", "reason": "cve_reachability:pyjwt->app/auth.py"}
        ],
        "ordering_source": "deterministic",
        "ordering_rationale": "",
        "deterministic_order": ["cve-CVE-1", "finding-0"],
    }
    base.update(overrides)
    return base


def test_returns_none_before_a6_has_run():
    assert build_repair_plan(_state()) is None


def test_deterministic_order_is_carried_through_even_on_the_llm_path():
    dag = _dag(
        execution_order=["finding-0", "cve-CVE-1"],
        ordering_source="llm",
        deterministic_order=["cve-CVE-1", "finding-0"],
    )
    result = build_repair_plan(_state(fix_dag=dag))
    assert result["deterministicOrder"] == ["cve-CVE-1", "finding-0"]


def test_deterministic_order_defaults_to_empty_on_a_run_predating_the_field():
    dag = _dag()
    del dag["deterministic_order"]
    result = build_repair_plan(_state(fix_dag=dag))
    assert result["deterministicOrder"] == []


def test_empty_plan_is_a_real_answer_not_a_missing_one():
    result = build_repair_plan(_state(fix_dag={"nodes": [], "execution_order": []}))
    assert result is not None
    assert result["steps"] == []


def test_steps_are_ordered_by_execution_order():
    result = build_repair_plan(_state(fix_dag=_dag()))
    assert [s["issueId"] for s in result["steps"]] == ["cve-CVE-1", "finding-0"]
    assert [s["position"] for s in result["steps"]] == [1, 2]
    assert all(s["ordered"] for s in result["steps"])


def test_why_joins_a_static_finding_by_issue_id():
    state = _state(
        fix_dag=_dag(),
        static_report={
            "prioritized": [
                {
                    "id": "finding-0", "file": "app/auth.py", "message": "hardcoded secret",
                    "severity": 0.9, "severity_measured": True, "tools": ["bandit"],
                }
            ]
        },
    )
    result = build_repair_plan(state)
    finding_step = next(s for s in result["steps"] if s["issueId"] == "finding-0")
    assert finding_step["why"] == {
        "kind": "static_finding",
        "message": "hardcoded secret",
        "severity": 0.9,
        "severityMeasured": True,
        "tools": ["bandit"],
    }


def test_why_joins_a_cve_by_reversing_the_issue_id():
    state = _state(
        fix_dag=_dag(),
        cve_report={
            "findings": [
                {
                    "cve_id": "CVE-1", "package": "pyjwt", "severity": "HIGH",
                    "installed_version": "1.0.0", "reach_path": ["app/auth.py"],
                }
            ]
        },
    )
    result = build_repair_plan(state)
    cve_step = next(s for s in result["steps"] if s["issueId"] == "cve-CVE-1")
    assert cve_step["why"] == {
        "kind": "cve", "package": "pyjwt", "severity": "HIGH",
        "installedVersion": "1.0.0", "reachPath": ["app/auth.py"],
    }


def test_why_is_none_when_neither_source_matches():
    """The synthetic `fix-0` catch-all node build_fix_nodes emits when nothing was named."""
    state = _state(fix_dag=_dag(
        nodes=[{"issue_id": "fix-0", "files": ["app/x.py"], "depends_on": []}],
        execution_order=["fix-0"],
        dependency_edges=[],
    ))
    result = build_repair_plan(state)
    assert result["steps"][0]["why"] is None


def test_dependency_edges_are_joined_as_incoming_per_step():
    result = build_repair_plan(_state(fix_dag=_dag()))
    finding_step = next(s for s in result["steps"] if s["issueId"] == "finding-0")
    assert finding_step["incomingEdges"] == [
        {"fromIssue": "cve-CVE-1", "reason": "cve_reachability:pyjwt->app/auth.py"}
    ]
    cve_step = next(s for s in result["steps"] if s["issueId"] == "cve-CVE-1")
    assert cve_step["incomingEdges"] == []


def test_conflict_batches_are_joined_symmetrically_excluding_self():
    result = build_repair_plan(
        _state(fix_dag=_dag(conflict_batches=[["cve-CVE-1", "finding-0"]]))
    )
    by_id = {s["issueId"]: s for s in result["steps"]}
    assert by_id["cve-CVE-1"]["conflictsWith"] == ["finding-0"]
    assert by_id["finding-0"]["conflictsWith"] == ["cve-CVE-1"]


def test_nodes_the_llm_omitted_are_appended_not_dropped():
    """The LLM ordering path is used unvalidated — it can name a subset of nodes."""
    dag = _dag(
        execution_order=["cve-CVE-1"],  # omits finding-0
        ordering_source="llm",
        ordering_rationale="fix the CVE first",
    )
    result = build_repair_plan(_state(fix_dag=dag))

    assert [s["issueId"] for s in result["steps"]] == ["cve-CVE-1", "finding-0"]
    omitted = next(s for s in result["steps"] if s["issueId"] == "finding-0")
    assert omitted["ordered"] is False
    assert omitted["position"] == 2


def test_ordering_source_and_rationale_are_carried_through():
    result = build_repair_plan(
        _state(fix_dag=_dag(ordering_source="llm", ordering_rationale="dependency order respected"))
    )
    assert result["orderingSource"] == "llm"
    assert result["orderingRationale"] == "dependency order respected"


def test_ordering_source_is_none_on_a_run_predating_the_field():
    dag = _dag()
    del dag["ordering_source"]
    del dag["ordering_rationale"]
    result = build_repair_plan(_state(fix_dag=dag))
    assert result["orderingSource"] is None
    assert result["orderingRationale"] == ""


def test_execution_authority_states_a7_reads_only_the_first_step():
    result = build_repair_plan(_state(fix_dag=_dag()))
    assert result["executionAuthority"]["field"] == "execution_order[0]"
    assert "A7" in result["executionAuthority"]["consumedBy"]


def test_no_confidence_field_is_ever_fabricated():
    result = build_repair_plan(_state(fix_dag=_dag()))
    assert "confidence" not in result
    assert all("confidence" not in s for s in result["steps"])


def test_is_handoff_target_is_true_only_for_execution_order_zero():
    """`execution_order[0]` is the one value `a7_code_generation.py` reads —
    every other step, however early in the list, is not the handoff."""
    result = build_repair_plan(_state(fix_dag=_dag()))
    by_id = {s["issueId"]: s["isHandoffTarget"] for s in result["steps"]}
    assert by_id == {"cve-CVE-1": True, "finding-0": False}


def test_is_handoff_target_is_false_for_everyone_when_execution_order_is_empty():
    dag = _dag(execution_order=[])
    result = build_repair_plan(_state(fix_dag=dag))
    assert all(s["isHandoffTarget"] is False for s in result["steps"])


def test_is_handoff_target_follows_the_real_order_not_list_position():
    """A node the LLM omitted can still occupy `steps[0]` in a starved plan
    (e.g. the LLM named only a later node) — `isHandoffTarget` must track
    `execution_order[0]` itself, not "whichever step renders first"."""
    dag = _dag(
        nodes=[
            {"issue_id": "a", "files": ["x.py"], "depends_on": []},
            {"issue_id": "b", "files": ["y.py"], "depends_on": []},
        ],
        execution_order=["b"],  # omits "a" — "a" still sorts before "b" alphabetically
        dependency_edges=[],
        ordering_source="llm",
    )
    result = build_repair_plan(_state(fix_dag=dag))
    by_id = {s["issueId"]: s["isHandoffTarget"] for s in result["steps"]}
    assert by_id == {"a": False, "b": True}


def test_empty_plan_has_no_handoff_target_to_report():
    result = build_repair_plan(_state(fix_dag={"nodes": [], "execution_order": []}))
    assert result["steps"] == []
