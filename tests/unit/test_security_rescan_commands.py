"""A9 reexecution commands must quote paths defensively."""

from pathlib import Path

from backend.services.security_rescan_commands import build_security_rescan_command


def test_security_command_quotes_paths_with_spaces():
    cmd, timeout = build_security_rescan_command([Path("/tmp/my repo/src")])
    assert timeout == 150
    assert "'/tmp/my repo/src'" in cmd or '"/tmp/my repo/src"' in cmd
    assert "my repo" in cmd
    assert " && " in cmd
    assert "bandit" in cmd
    assert "semgrep" in cmd
