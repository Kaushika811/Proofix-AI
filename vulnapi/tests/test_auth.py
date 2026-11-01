import base64
import json
import time


from vulnapi.auth import validate_token
from vulnapi.middleware import admin_middleware


def _make_token(sub: str = "user1", exp: int | None = None) -> str:
    if exp is None:
        exp = int(time.time()) + 3600
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub, "exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_valid_token_accepted():
    token = _make_token(exp=int(time.time()) + 3600)
    assert validate_token(token) is True


def test_expired_token_rejected():
    """Bug 1 demo: expired token should be rejected but auth.py accepts it."""
    token = _make_token(exp=int(time.time()) - 3600)
    assert validate_token(token) is False


def test_admin_requires_auth():
    """Bug 5 demo: admin route should require auth but ADMIN_MODE bypasses it."""
    headers = {}
    # Without ADMIN_MODE, should fail (no token)
    assert admin_middleware(headers, "/admin/users") is False


def test_admin_with_valid_token():
    token = _make_token()
    headers = {"Authorization": f"Bearer {token}"}
    assert admin_middleware(headers, "/admin/users") is True
