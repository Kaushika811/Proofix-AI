from backend.services.mci_verifier import (
    file_paths_equivalent,
    find_phantom_file_references,
    generate_description_from_diff,
    normalize_path_token,
    verify_mci,
)

AUTH_DIFF = """\
--- a/vulnapi/auth.py
+++ b/vulnapi/auth.py
@@ -23,7 +23,8 @@
     if "sub" not in payload:
         return False
-    # Missing: if payload.get("exp", 0) < time.time(): return False
+    if payload.get("exp", 0) < time.time():
+        return False
     return True

 def validate_token(token: str) -> bool:
"""


def test_generate_description_from_diff():
    diff = "--- a/src/myapp/auth.py\n+++ b/src/myapp/auth.py\n@@ -1,3 +1,5 @@\n+import time\n"
    desc = generate_description_from_diff(diff)
    assert "auth.py" in desc


def test_auth_basename_matches_repo_relative_path_not_phantom():
    description = (
        "The root cause is that the validate_token function in auth.py "
        "does not properly validate expired tokens."
    )
    ok, phantoms = verify_mci(description, AUTH_DIFF)
    assert ok is True
    assert phantoms == set()


def test_windows_path_matches_posix_path_not_phantom():
    diff = "--- a/vulnapi\\auth.py\n+++ b/vulnapi\\auth.py\n@@ -1 +1,2 @@\n+pass\n"
    description = "Fix validate_token in vulnapi/auth.py"
    ok, phantoms = verify_mci(description, diff)
    assert ok is True
    assert phantoms == set()


def test_case_insensitive_path_normalization_not_phantom():
    description = "Update VulnAPI/Auth.py to reject expired tokens"
    diff = "--- a/vulnapi/auth.py\n+++ b/vulnapi/auth.py\n@@ -1 +1,2 @@\n+pass\n"
    ok, phantoms = verify_mci(description, diff)
    assert ok is True
    assert phantoms == set()


def test_genuinely_different_files_are_phantom():
    description = "Fix auth.py and config.py"
    diff = "--- a/vulnapi/auth.py\n+++ b/vulnapi/auth.py\n@@ -1 +1,2 @@\n+pass\n"
    ok, phantoms = verify_mci(description, diff)
    assert ok is False
    assert any("config.py" in phantom for phantom in phantoms)


def test_file_paths_equivalent_matrix():
    assert file_paths_equivalent("auth.py", "vulnapi/auth.py") is True
    assert file_paths_equivalent("vulnapi/auth.py", "auth.py") is True
    assert file_paths_equivalent(r"vulnapi\auth.py", "vulnapi/auth.py") is True
    assert file_paths_equivalent("VulnAPI/Auth.py", "vulnapi/auth.py") is True
    assert file_paths_equivalent("auth.py", "vulnapi/config.py") is False
    assert file_paths_equivalent("api.py", "vulnapi/auth.py") is False


def test_normalize_path_token_strips_diff_prefixes():
    assert normalize_path_token("a/vulnapi/auth.py") == "vulnapi/auth.py"
    assert normalize_path_token(r"b\vulnapi\auth.py") == "vulnapi/auth.py"


def test_find_phantom_file_references():
    phantoms = find_phantom_file_references(
        {"auth.py", "config.py"},
        {"vulnapi/auth.py"},
    )
    assert phantoms == {"config.py"}


def test_run_like_description_with_generated_diff_not_phantom():
    description_why = (
        "The root cause is that the validate_token function in auth.py "
        "does not properly validate expired tokens."
    )
    description_what = generate_description_from_diff(AUTH_DIFF)
    ok, phantoms = verify_mci(f"{description_why} {description_what}", AUTH_DIFF)
    assert ok is True
    assert phantoms == set()
