"""Bug 4: Unsafe pickle deserialization — A9 trap if fix adds path traversal."""

import json
import pickle
from pathlib import Path


def deserialize_data(data: bytes) -> object:
    """BUG: pickle.loads on untrusted input."""
    return pickle.loads(data)


def deserialize_json(data: bytes) -> object:
    return json.loads(data.decode("utf-8"))


def load_file_contents(path: str) -> str:
    """Trap for A9: unsanitized path if A7 'fixes' pickle with file read."""
    p = Path(path)
    return p.read_text(encoding="utf-8")


def safe_load_json(data: bytes) -> object:
    return json.loads(data.decode("utf-8"))
