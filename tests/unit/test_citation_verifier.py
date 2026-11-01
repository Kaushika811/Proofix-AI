from pathlib import Path

import pytest

from backend.services.citation_verifier import verify_all_citations_with_metrics
from backend.services.citation_verifier import (
    resolve_citation_path,
    verify_citation,
)
from backend.models.root_cause import Citation, RootCauseBrief
from backend.services.evidence_investigation import _source_items

VULNAPI = Path(__file__).parent.parent.parent / "vulnapi"
AUTH_SOURCE = (VULNAPI / "vulnapi/auth.py").read_text(encoding="utf-8")

SIG = {
    "files": {
        "vulnapi/auth.py": {"path": "vulnapi/auth.py", "role": "auth-boundary"},
        "vulnapi/config.py": {"path": "vulnapi/config.py", "role": "config-surface"},
    }
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    auth_dir = tmp_path / "vulnapi"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.py").write_text(AUTH_SOURCE, encoding="utf-8")
    (auth_dir / "config.py").write_text("SECRET = 'hardcoded'\n", encoding="utf-8")
    return tmp_path


def test_auth_basename_resolves_to_repo_relative_path(repo: Path):
    resolved = resolve_citation_path(repo, "auth.py", sig=SIG)
    assert resolved == "vulnapi/auth.py"


def test_windows_path_resolves(repo: Path):
    resolved = resolve_citation_path(repo, r"vulnapi\auth.py", sig=SIG)
    assert resolved == "vulnapi/auth.py"


def test_basename_only_reference_verifies(repo: Path):
    citation = {
        "file": "auth.py",
        "line": 19,
        "claim": "validate_token() missing expiry validation",
    }
    result, method = verify_citation(repo, citation, sig=SIG)
    assert result["verified"] is True
    assert result["file"] == "vulnapi/auth.py"
    assert method in {"verified_exact", "verified_line_window", "verified_ast"}


def test_moved_line_numbers_use_line_window(repo: Path):
    shifted = "\n\n" + AUTH_SOURCE
    (repo / "vulnapi" / "auth.py").write_text(shifted, encoding="utf-8")
    citation = {
        "file": "vulnapi/auth.py",
        "line": 19,
        "claim": "def validate_token(token: str) -> bool:",
    }
    result, method = verify_citation(repo, citation, sig=SIG)
    assert result["verified"] is True
    assert method == "verified_line_window"
    assert result["line"] != 19


def test_function_moved_lower_uses_ast(repo: Path):
    prefix = "# header comment\n\n# extra note\n\n"
    (repo / "vulnapi" / "auth.py").write_text(prefix + AUTH_SOURCE, encoding="utf-8")
    citation = {
        "file": "auth.py",
        "line": 19,
        "claim": "validate_token() does not validate expired tokens",
    }
    result, method = verify_citation(repo, citation, sig=SIG)
    assert result["verified"] is True
    assert method in {"verified_ast", "verified_line_window", "verified_exact"}


def test_fingerprint_match_elsewhere(repo: Path):
    target_line = "    if payload.get(\"exp\", 0) < time.time(): return False"
    modified = AUTH_SOURCE.replace(
        "# Missing: if payload.get(\"exp\", 0) < time.time(): return False",
        target_line,
    )
    (repo / "vulnapi" / "auth.py").write_text(modified, encoding="utf-8")
    citation = {
        "file": "vulnapi/auth.py",
        "line": 5,
        "claim": target_line,
    }
    result, method = verify_citation(repo, citation, sig=SIG)
    assert result["verified"] is True
    assert method in {"verified_fingerprint", "verified_line_window", "verified_exact"}


def test_truly_invalid_citation_unresolved(repo: Path):
    citation = {
        "file": "missing/foo.py",
        "line": 10,
        "claim": "nonexistent function",
    }
    result, method = verify_citation(repo, citation, sig=SIG)
    assert result["verified"] is False
    assert method == "unresolved"


def test_located_function_does_not_prove_false_claim(tmp_path: Path):
    (tmp_path / "calculator.py").write_text("def calculate():\n    return 1\n", encoding="utf-8")
    result, method = verify_citation(tmp_path, {
        "file": "calculator.py", "line": 1,
        "claim": "calculate() deletes all customer records from the database",
        "claim_support_status": "supported",
    })
    assert method == "verified_exact"
    assert result["source_located"] is True
    assert result["verified"] is True  # legacy anchor field
    assert result["claim_support_status"] == "unverified"
    item = _source_items(RootCauseBrief(citations=[Citation(**result)]))[0]
    assert item.stance == "neutral"
    assert item.detail["claimSupportStatus"] == "unverified"


def test_wrong_file_for_auth_claim_unresolved(repo: Path):
    citation = {
        "file": "vulnapi/config.py",
        "line": 1,
        "claim": "validate_token() missing expiry validation",
    }
    result, method = verify_citation(repo, citation, sig=SIG)
    assert result["verified"] is False
    assert method == "unresolved"


def test_metrics_sum_to_total(repo: Path):
    citations = [
        {
            "file": "auth.py",
            "line": 19,
            "claim": "validate_token() missing expiry validation",
        },
        {
            "file": "missing/foo.py",
            "line": 1,
            "claim": "does not exist",
        },
    ]
    validated, metrics = verify_all_citations_with_metrics(repo, citations, sig=SIG)
    assert len(validated) == 2
    assert metrics["total_citations"] == 2
    assert (
        metrics["verified_exact"]
        + metrics["verified_line_window"]
        + metrics["verified_ast"]
        + metrics["verified_fingerprint"]
        + metrics["unresolved"]
    ) == metrics["total_citations"]
    assert metrics["unresolved"] == 1


def test_validator_wrapper_returns_metrics(repo: Path):
    citations = [
        {
            "file": "auth.py",
            "line": 19,
            "claim": "validate_token() missing expiry validation",
        }
    ]
    validated, metrics = verify_all_citations_with_metrics(repo, citations, sig=SIG)
    assert validated[0]["verified"] is True
    assert metrics["total_citations"] == 1
    assert metrics["unresolved"] == 0


@pytest.mark.skipif(not VULNAPI.exists(), reason="vulnapi demo repo not present")
def test_vulnapi_repo_auth_basename_verifies():
    citations = [
        {
            "file": "auth.py",
            "line": 19,
            "claim": "validate_token() missing expiry validation",
        }
    ]
    validated, metrics = verify_all_citations_with_metrics(VULNAPI, citations, sig=SIG)
    assert validated[0]["verified"] is True
    assert validated[0]["file"] == "vulnapi/auth.py"
    assert metrics["unresolved"] == 0


# Moved here when `citation_validator.py` was deleted (B-B11). It was a
# pass-through shim whose `validate_all_citations*` functions returned this
# module's `verify_all_citations_with_metrics` unchanged, so these tests were
# exercising this code through one pointless hop.

def test_verification_resolves_a_bare_basename_through_the_sig(tmp_path):
    auth_dir = tmp_path / "vulnapi"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.py").write_text("def validate_token():\n    return True\n", encoding="utf-8")
    sig = {"files": {"vulnapi/auth.py": {"path": "vulnapi/auth.py"}}}
    validated, _metrics = verify_all_citations_with_metrics(
        tmp_path,
        [{"file": "auth.py", "line": 1, "claim": "validate_token() bug"}],
        sig=sig,
    )
    assert validated[0]["verified"] is True
    assert validated[0]["file"] == "vulnapi/auth.py"


def test_verification_reports_metrics_alongside_the_citations(tmp_path):
    auth_dir = tmp_path / "vulnapi"
    auth_dir.mkdir(parents=True)
    (auth_dir / "auth.py").write_text("def validate_token():\n    return True\n", encoding="utf-8")
    validated, metrics = verify_all_citations_with_metrics(
        tmp_path,
        [{"file": "auth.py", "line": 1, "claim": "validate_token() bug"}],
    )
    assert validated[0]["verified"] is True
    assert metrics["total_citations"] == 1
