import os


from vulnapi.config import get_secret, is_secret_configured, load_secret_from_env


def test_secret_from_env():
    """Bug 3 demo: secret should come from env, not be hardcoded."""
    secret = get_secret()
    assert secret != "hardcoded-secret-12345"
    assert load_secret_from_env() != "" or os.environ.get("API_SECRET")


def test_secret_not_hardcoded():
    assert "hardcoded" not in get_secret().lower()


def test_is_secret_configured_overfit():
    """Overfit-prone test: passes with any non-empty secret."""
    assert is_secret_configured() is True
