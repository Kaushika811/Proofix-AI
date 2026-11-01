"""Timeouts must reap the whole tool process group, including forked workers."""
import asyncio
import os

import pytest

from backend.services.subprocess_runner import PYTHON, run_command


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != 'posix', reason='POSIX process groups')
async def test_timeout_stops_descendants(tmp_path):
    marker = tmp_path / 'orphan.txt'
    child = f'import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text("orphan")'
    parent = f'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", {child!r}]); time.sleep(10)'
    code, _, error = await run_command([PYTHON, '-c', parent], timeout=0.3)
    assert code == -1 and error == 'timeout'
    await asyncio.sleep(1)
    assert not marker.exists()
