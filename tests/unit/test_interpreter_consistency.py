"""Every Python subprocess runs under the same interpreter.

The probe's whole value is answering the question A3.5 will later ask. Two
different interpreters are two different questions, so a disagreement between
them makes the probe worse than useless: it either blocks a runnable repository
or clears one that cannot run.

They disagreed by way of `"python"`, a bare name resolved through `PATH` — which
is whatever the server process inherited, and on a machine where ProoFix runs
from a virtualenv is typically *not* the interpreter ProoFix is running under.
The probe reported `No module named 'pytest'` while ProoFix's own interpreter
had pytest installed, in a message claiming to describe "the interpreter ProoFix
would run its tests with".
"""

import ast
import sys
from pathlib import Path

from backend.services.subprocess_runner import PYTHON

BACKEND = Path(__file__).parent.parent.parent / "backend"

#: Modules that shell out to Python to run or inspect the target's test suite.
RUNNERS = {
    "environment_probe.py",
    "a3_5_reproduction.py",
    "scoped_validation.py",
    "a8_mutation_validator.py",
}


def test_python_is_the_interpreter_proofix_runs_under():
    assert PYTHON == sys.executable


def test_no_runner_invokes_a_bare_python():
    """The literal `"python"` as argv[0] anywhere in these modules."""
    offenders: list[str] = []

    for path in BACKEND.rglob("*.py"):
        if path.name not in RUNNERS:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.List) or not node.elts:
                continue
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in {"python", "python3"}:
                offenders.append(f"{path.name}:{first.lineno}")

    assert offenders == [], f"bare interpreter in a command: {offenders}"


def test_the_probe_and_reproduction_agree():
    """Pinned by reading both call sites, since the cost of drift is silent."""
    probe = (BACKEND / "services" / "environment_probe.py").read_text()
    reproduction = (BACKEND / "agents" / "a3_5_reproduction.py").read_text()

    assert "[PYTHON, \"-c\", \"import pytest" in probe
    assert "PYTHON,\n            \"-m\",\n            \"pytest\"," in reproduction
