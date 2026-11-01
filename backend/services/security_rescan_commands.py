"""Build literal security rescan commands with defensively quoted paths."""

from __future__ import annotations

import shlex
from pathlib import Path

from backend.services.repo_layout import bandit_exclude_arg, semgrep_exclude_args


def build_security_rescan_command(scan_targets: list[Path]) -> tuple[str, int]:
    """
    Return (reexecution_command, timeout_seconds).
    Every path token is shell-quoted for safe copy-paste (no interpolation).
    Mirrors the real invocation in `a9_security_rescan.py` exactly, exclude
    flags included — a re-execution command missing them would rescan
    vendored dependency source the real run never scored.
    """
    if not scan_targets:
        return "", 150

    bandit_tokens = ["bandit", "-f", "json", "-q", "-x", bandit_exclude_arg()]
    for target in scan_targets:
        bandit_tokens.extend(["-r", str(target)])

    semgrep_tokens = ["semgrep", "--config=auto", "--json", *semgrep_exclude_args()]
    for target in scan_targets:
        semgrep_tokens.append(str(target))

    bandit_cmd = " ".join(shlex.quote(t) for t in bandit_tokens)
    semgrep_cmd = " ".join(shlex.quote(t) for t in semgrep_tokens)
    return f"{bandit_cmd} && {semgrep_cmd}", 150
