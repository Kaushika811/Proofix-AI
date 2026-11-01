"""Bug 5: Admin route skips auth when ADMIN_MODE=true (not set in test env)."""

import os

from vulnapi.auth import validate_token


def check_auth(headers: dict) -> bool:
    token = headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return False
    return validate_token(token)


def admin_middleware(headers: dict, path: str) -> bool:
    """BUG: skips auth when ADMIN_MODE=true — not set during pytest."""
    if path.startswith("/admin"):
        if os.environ.get("ADMIN_MODE", "").lower() == "true":
            return True  # BUG: bypasses auth entirely
        return check_auth(headers)
    return check_auth(headers)


def public_middleware(headers: dict, path: str) -> bool:
    if path.startswith("/public"):
        return True
    return check_auth(headers)


def route_request(path: str, headers: dict) -> tuple[bool, str]:
    if path.startswith("/admin"):
        authorized = admin_middleware(headers, path)
        return authorized, "admin"
    if path.startswith("/public"):
        return True, "public"
    return check_auth(headers), "protected"
