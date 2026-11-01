"""What the privacy guard actually covers (B-B04).

A5.5 is the only point in the pipeline where secrets are masked before an LLM
call, so its coverage *is* the privacy claim. The guard ran on extracted code
and stopped there. Two consequences, both recorded in the historical QA report:

  * `acceptance_criteria`, `contracts`, `validation_requirements` and
    `patch_constraints` reach the patch prompt unscanned. They are derived from
    exception messages and failing-test names — precisely where a leaked
    credential surfaces. A JWT reached `acceptance_criteria[2]` with the
    package reporting `privacy_guard_status: "clean"` and zero redactions.

  * The scans that *did* run on free text threw their redactions away, so a
    secret masked in a traceback was removed from the prompt and then denied by
    the ledger. An unrecorded redaction is indistinguishable from no secret,
    which is the one distinction this ledger exists to make.
"""

import pytest

from backend.services.context_package import PackageInputs, build_package

# Shaped like a real leak: a token in the message pytest printed.
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"

SOURCE = '''\
import time


def validate(token):
    return True
'''


def _inputs(tmp_path, **overrides) -> PackageInputs:
    (tmp_path / "auth.py").write_text(SOURCE, encoding="utf-8")
    base = dict(
        repo_path=tmp_path,
        target_file="auth.py",
        target_function="validate",
        root_cause_summary="validate() never checks expiry",
    )
    base.update(overrides)
    return PackageInputs(**base)


def _ledger_files(package) -> set[str]:
    return {r.file for r in package.redactions}


class TestPromptBoundStringsAreScanned:
    def test_a_secret_in_acceptance_criteria_is_masked(self, tmp_path):
        """The exact field named in the report."""
        package = build_package(
            _inputs(
                tmp_path,
                acceptance_criteria=[
                    "the failing test must pass",
                    f"reject the token {JWT}",
                ],
            )
        )

        assert JWT not in " ".join(package.acceptance_criteria)
        assert package.privacy_guard_status == "masked"
        assert "acceptance_criteria[1]" in _ledger_files(package)

    @pytest.mark.parametrize(
        "field,ledger",
        [
            ("contracts", "contracts[0]"),
            ("validation_requirements", "validation_requirements[0]"),
            ("patch_constraints", "patch_constraints[0]"),
        ],
    )
    def test_every_other_prompt_bound_list_is_scanned(self, tmp_path, field, ledger):
        package = build_package(_inputs(tmp_path, **{field: [f"uses key {AWS_KEY}"]}))

        assert AWS_KEY not in str(getattr(package, field))
        assert package.privacy_guard_status == "masked"
        assert ledger in _ledger_files(package)

    def test_the_ledger_names_the_field_not_an_empty_path(self, tmp_path):
        """These strings have no source file; an empty path makes the audit
        unreadable at the point it matters most."""
        package = build_package(
            _inputs(tmp_path, acceptance_criteria=[f"token {JWT}"])
        )

        assert all(r.file for r in package.redactions)


class TestMaskingIsAlwaysRecorded:
    def test_a_secret_masked_in_the_traceback_reaches_the_ledger(self, tmp_path):
        """`_sanitize_evidence` masked and then discarded what it found, so the
        package reported `clean` while masking had happened."""
        package = build_package(
            _inputs(
                tmp_path,
                runtime_evidence={"traceback": f"AssertionError: accepted {JWT}"},
            )
        )

        assert JWT not in str(package.runtime_evidence)
        assert package.privacy_guard_status == "masked"
        assert package.redactions

    def test_a_secret_in_the_root_cause_summary_reaches_the_ledger(self, tmp_path):
        package = build_package(
            _inputs(tmp_path, root_cause_summary=f"the key {AWS_KEY} is hardcoded")
        )

        assert AWS_KEY not in package.root_cause_summary
        assert package.privacy_guard_status == "masked"

    def test_a_clean_package_still_reports_clean(self, tmp_path):
        """The status must remain a measurement, not a default."""
        package = build_package(
            _inputs(tmp_path, acceptance_criteria=["the failing test must pass"])
        )

        assert package.privacy_guard_status == "clean"
        assert package.redactions == []


class TestFailedGuardWins:
    def test_failed_is_never_downgraded_to_masked(self, tmp_path, monkeypatch):
        """A guard that errored knows nothing about what it did or did not see.

        Recomputing the status from the ledger must not let a redaction found
        elsewhere overwrite that.
        """
        import backend.services.context_package as module

        def explode(*args, **kwargs):
            raise RuntimeError("guard exploded")

        monkeypatch.setattr(module, "_sanitize_extraction", explode)

        package = build_package(
            _inputs(tmp_path, acceptance_criteria=[f"token {JWT}"])
        )

        assert package.privacy_guard_status == "failed"
