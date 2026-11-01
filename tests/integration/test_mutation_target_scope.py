"""A8 mutation still runs when other seeded issues fail their own tests."""

import importlib.util
from unittest.mock import MagicMock

import pytest

from backend.agents.a8_mutation_validator import A8MutationValidatorAgent
from backend.config import Settings


@pytest.mark.asyncio
async def test_mutmut_scores_selected_issue_with_sibling_imports_and_baseline_failures(tmp_path):
    if importlib.util.find_spec("mutmut") is None:
        pytest.skip("mutmut is not installed")

    package = tmp_path / "shopcore"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "auth.py").write_text(
        "def is_session_active(expires_at, now):\n"
        "    return expires_at > now\n"
    )
    (package / "inventory.py").write_text(
        "def stock_after_reservation(available, requested):\n"
        "    return available - requested\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_auth.py").write_text(
        "from shopcore.auth import is_session_active\n\n"
        "def test_expired():\n"
        "    assert is_session_active(9, 10) is False\n"
        "    assert is_session_active(10, 10) is False\n"
        "    assert is_session_active(11, 10) is True\n"
    )
    (tests / "test_inventory.py").write_text(
        "from shopcore.inventory import stock_after_reservation\n\n"
        "def test_oversell():\n"
        "    assert stock_after_reservation(1, 2) is None\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")

    agent = A8MutationValidatorAgent(MagicMock(), Settings(stub_mode=True))
    result, command = await agent._run_mutmut(
        tmp_path,
        {"patches": [{"file": "shopcore/auth.py"}]},
        target_test="tests/test_auth.py::test_expired",
        baseline_failures=["tests/test_inventory.py::test_oversell"],
    )

    assert result.status == "scored", result.unavailable_reason
    assert result.total_mutants is not None and result.total_mutants > 0
    assert result.killed_mutants == result.total_mutants
    assert result.mutation_score == 1.0
    assert "mutmut results --all true" in command


@pytest.mark.asyncio
async def test_multiple_flat_modules_toml_scope_and_checkout_preservation(tmp_path):
    (tmp_path / 'auth.py').write_text('from helper import LIMIT\n\ndef valid(n):\n    return n > LIMIT\n')
    (tmp_path / 'helper.py').write_text('LIMIT = 0\n')
    (tmp_path / 'price.py').write_text('def total(n):\n    return n * 2\n')
    (tmp_path / 'test_app.py').write_text(
        'from auth import valid\nfrom price import total\n\n'
        'def test_valid():\n    assert not valid(-1)\n    assert not valid(0)\n    assert valid(1)\n\n'
        'def test_total():\n    assert total(3) == 6\n')
    (tmp_path / 'pyproject.toml').write_text('[tool.mutmut]\nsource_paths = ["missing"]\nonly_mutate = ["missing.py"]\n')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    agent = A8MutationValidatorAgent(MagicMock(), Settings(stub_mode=True))
    result, _ = await agent._run_mutmut(tmp_path, {'patches': [{'file':'auth.py'}, {'file':'price.py'}]})
    assert result.status == 'scored', result.unavailable_reason
    assert any('auth.' in m.mutant_id for m in result.mutants)
    assert any('price.' in m.mutant_id for m in result.mutants)
    assert not any('helper.' in m.mutant_id for m in result.mutants)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert after == before
