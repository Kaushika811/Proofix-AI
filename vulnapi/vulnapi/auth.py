"""Bug 1: Token validated without expiry check — spans auth/middleware/api."""

import base64
import json


def decode_token(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        return payload
    except Exception:
        return None


def validate_token(token: str) -> bool:
    """BUG: validates signature shape but NOT expiry."""
    payload = decode_token(token)
    if not payload:
        return False
    if "sub" not in payload:
        return False
    # Missing: if payload.get("exp", 0) < time.time(): return False
    return True


def get_token_subject(token: str) -> str | None:
    payload = decode_token(token)
    return payload.get("sub") if payload else None
