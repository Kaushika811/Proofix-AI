"""Shared path resolution — the single answer to "is this the same file?".

Consolidates behaviour previously duplicated across target_resolver,
citation_verifier and mci_verifier. Cases here pin the union of what those three
implementations were each relied upon to do.
"""

from backend.services.path_resolution import (
    basename,
    is_absolute,
    match_key,
    normalize_path_token,
    path_candidates,
    paths_equivalent,
    relative_to_repo,
    resolve_against_keys,
    resolve_existing,
    to_posix,
)

SIG_KEYS = ["vulnapi/auth.py", "vulnapi/api.py", "vulnapi/config.py"]


# -- normalization ---------------------------------------------------------


def test_normalizes_posix_paths():
    assert normalize_path_token("vulnapi/auth.py") == "vulnapi/auth.py"
    assert normalize_path_token("./vulnapi/auth.py") == "vulnapi/auth.py"
    assert normalize_path_token("/vulnapi/auth.py") == "vulnapi/auth.py"


def test_normalizes_windows_paths():
    assert normalize_path_token(r"vulnapi\auth.py") == "vulnapi/auth.py"
    assert normalize_path_token(r"b\vulnapi\auth.py") == "vulnapi/auth.py"
    assert normalize_path_token(r"C:\src\vulnapi\auth.py") == "c:/src/vulnapi/auth.py"


def test_normalization_is_case_insensitive():
    assert normalize_path_token("VulnAPI/Auth.py") == normalize_path_token("vulnapi/auth.py")


def test_strips_git_diff_prefixes():
    assert normalize_path_token("a/vulnapi/auth.py") == "vulnapi/auth.py"
    assert normalize_path_token("b/vulnapi/auth.py") == "vulnapi/auth.py"


def test_strips_quotes_and_backticks():
    assert normalize_path_token("`vulnapi/auth.py`") == "vulnapi/auth.py"
    assert normalize_path_token('"vulnapi/auth.py"') == "vulnapi/auth.py"
    assert normalize_path_token("'vulnapi/auth.py'") == "vulnapi/auth.py"


def test_collapses_dot_segments():
    assert normalize_path_token("vulnapi/./auth.py") == "vulnapi/auth.py"


def test_empty_and_degenerate_input():
    assert normalize_path_token("") == ""
    assert normalize_path_token("   ") == ""
    assert normalize_path_token(".") == ""
    assert normalize_path_token("/") == ""
    assert normalize_path_token(None) == ""


def test_to_posix_preserves_case():
    assert to_posix(r"b\VulnAPI\Auth.py") == "VulnAPI/Auth.py"


def test_basename():
    assert basename(r"vulnapi\auth.py") == "auth.py"
    assert basename("auth.py") == "auth.py"
    assert basename("") == ""


# -- absolute detection ----------------------------------------------------


def test_detects_absolute_paths():
    assert is_absolute("/tmp/repo/auth.py") is True
    assert is_absolute(r"C:\src\auth.py") is True
    assert is_absolute("C:/src/auth.py") is True
    assert is_absolute(r"\\server\share\auth.py") is True
    assert is_absolute("vulnapi/auth.py") is False
    assert is_absolute("") is False


# -- candidate generation --------------------------------------------------


def test_candidates_are_ordered_most_specific_first():
    assert path_candidates("/tmp/proofix_x/vulnapi/auth.py") == [
        "tmp/proofix_x/vulnapi/auth.py",
        "proofix_x/vulnapi/auth.py",
        "vulnapi/auth.py",
        "auth.py",
    ]


def test_candidates_from_windows_absolute_path():
    assert "vulnapi/auth.py" in path_candidates(r"C:\builds\repo\vulnapi\auth.py")
    assert "auth.py" in path_candidates(r"C:\builds\repo\vulnapi\auth.py")


def test_candidates_do_not_depend_on_directory_names():
    """The old implementations whitelisted the fixture repo's own dir name."""
    candidates = path_candidates("/build/acme_service/handlers/login.py")
    assert "handlers/login.py" in candidates
    assert "login.py" in candidates


def test_no_candidates_for_empty_input():
    assert path_candidates("") == []
    assert path_candidates("///") == []


# -- key matching ----------------------------------------------------------


def test_matches_exact_key():
    assert match_key("vulnapi/auth.py", SIG_KEYS) == "vulnapi/auth.py"


def test_matches_by_unique_suffix():
    assert match_key("auth.py", SIG_KEYS) == "vulnapi/auth.py"


def test_matches_case_insensitively():
    assert match_key("VulnAPI/Auth.py", SIG_KEYS) == "vulnapi/auth.py"


def test_ambiguous_basename_refuses_to_guess():
    keys = ["pkg_a/auth.py", "pkg_b/auth.py"]
    assert match_key("auth.py", keys) is None


def test_ambiguous_basename_still_resolves_with_a_qualified_path():
    keys = ["pkg_a/auth.py", "pkg_b/auth.py"]
    assert match_key("pkg_b/auth.py", keys) == "pkg_b/auth.py"


def test_match_key_with_no_keys():
    assert match_key("auth.py", []) is None
    assert match_key("", SIG_KEYS) is None


def test_resolve_against_keys_walks_candidates():
    assert resolve_against_keys("/tmp/x/vulnapi/auth.py", SIG_KEYS) == "vulnapi/auth.py"
    assert resolve_against_keys(r"C:\x\vulnapi\auth.py", SIG_KEYS) == "vulnapi/auth.py"
    assert resolve_against_keys("nowhere/missing.py", SIG_KEYS) is None


# -- filesystem-backed resolution -----------------------------------------


def test_relative_to_repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "auth.py"
    target.write_text("x = 1")
    assert relative_to_repo(tmp_path, str(target)) == "pkg/auth.py"


def test_relative_to_repo_rejects_outside_paths(tmp_path):
    other = tmp_path.parent / "elsewhere.py"
    assert relative_to_repo(tmp_path / "repo", str(other)) is None


def test_resolve_existing_finds_file_by_basename(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "auth.py").write_text("x = 1")
    assert resolve_existing(tmp_path, "pkg/auth.py") == "pkg/auth.py"


def test_resolve_existing_from_absolute_path(tmp_path):
    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "auth.py"
    target.write_text("x = 1")
    assert resolve_existing(tmp_path, str(target)) == "pkg/auth.py"


def test_resolve_existing_returns_none_for_missing(tmp_path):
    assert resolve_existing(tmp_path, "pkg/nope.py") is None
    assert resolve_existing(tmp_path, "") is None


# -- equivalence -----------------------------------------------------------


def test_equivalent_paths():
    assert paths_equivalent("auth.py", "vulnapi/auth.py") is True
    assert paths_equivalent("vulnapi/auth.py", "auth.py") is True
    assert paths_equivalent(r"vulnapi\auth.py", "vulnapi/auth.py") is True
    assert paths_equivalent("VulnAPI/Auth.py", "vulnapi/auth.py") is True


def test_non_equivalent_paths():
    assert paths_equivalent("auth.py", "vulnapi/config.py") is False
    assert paths_equivalent("api.py", "vulnapi/auth.py") is False


def test_qualified_paths_must_match_in_full():
    """Looser matching here would silently raise the MCI fidelity score."""
    assert paths_equivalent("pkg_a/auth.py", "pkg_b/auth.py") is False


def test_empty_is_never_equivalent():
    assert paths_equivalent("", "auth.py") is False
    assert paths_equivalent("auth.py", "") is False
    assert paths_equivalent("", "") is False
