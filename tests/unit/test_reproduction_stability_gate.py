"""Hard gate: A3.5 targeted reproduction command must be stable 10/10 on base commit."""

from pathlib import Path

import pytest

from backend.services.git_service import get_head_sha
from backend.services.reproduction_commands import build_targeted_reproduction_command
from backend.services.subprocess_runner import run_command

VULNAPI = Path(__file__).parent.parent.parent / "vulnapi"
KNOWN_FAILING_TEST = "tests/test_auth.py::test_expired_token_rejected"


@pytest.mark.proof_gate
@pytest.mark.asyncio
async def test_reproduction_command_stable_10_of_10():
    if not VULNAPI.exists():
        pytest.skip("vulnapi demo repo not present")

    repo = VULNAPI.resolve()
    base_sha = get_head_sha(repo)
    assert base_sha, "vulnapi must be a git repo with HEAD"

    command = build_targeted_reproduction_command(KNOWN_FAILING_TEST)
    exit_codes: list[int] = []

    for _ in range(10):
        code, _stdout, _stderr = await run_command(
            command.split(),
            cwd=repo,
            timeout=120,
        )
        exit_codes.append(code)

    assert len(set(exit_codes)) == 1, f"Flaky reproduction: exit codes varied {exit_codes}"
    assert exit_codes[0] != 0, "Expected failing test on base commit"
