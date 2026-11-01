"""Pin the exact acceptance test selected by the issue contract."""

import hashlib
from pathlib import Path


def pin_acceptance_test(repo: Path, nodeid: str) -> dict:
    relative = nodeid.split("::", 1)[0]
    path = (repo / relative).resolve()
    if not path.is_relative_to(repo.resolve()) or not path.is_file():
        raise ValueError("Acceptance test is absent or outside the repository")
    return {"path": relative, "nodeid": nodeid, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def acceptance_test_integrity_error(repo: Path, artifact: dict | None) -> str | None:
    if not artifact:
        return None
    try:
        path = (repo / artifact["path"]).resolve()
        intact = (path.is_relative_to(repo.resolve()) and path.is_file()
                  and hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"])
    except (KeyError, OSError, TypeError):
        intact = False
    return None if intact else "Acceptance test was removed or changed after reproduction"
