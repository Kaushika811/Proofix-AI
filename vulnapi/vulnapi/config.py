"""Bug 3: Hardcoded API secret — test is overfit-prone (checks len > 0 not source)."""

import os

# BUG: hardcoded secret
SECRET = "hardcoded-secret-12345"
API_KEY = "sk-live-abc123def456"


def get_secret() -> str:
    return SECRET


def get_api_key() -> str:
    return API_KEY


def is_secret_configured() -> bool:
    """Overfit-prone: passes if secret is any non-empty string."""
    secret = get_secret()
    return len(secret) > 0


def load_secret_from_env() -> str:
    """Intended fix path: read from environment."""
    return os.environ.get("API_SECRET", "")
