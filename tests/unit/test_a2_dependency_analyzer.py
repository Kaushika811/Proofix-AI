"""A2 — Dependency Analyzer.

Covers what the dependency-risk UI depends on: the manifest/ecosystem facts,
the total-dependencies count (distinct from the advisory count), the installed
version per finding, and the real reachable-file list now recorded on
`reach_path` instead of a bare boolean.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.agents.a2_dependency_analyzer import A2DependencyAnalyzerAgent
from backend.config import Settings
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

VULNAPI = Path(__file__).parent.parent.parent / "vulnapi"


@pytest_asyncio.fixture
async def redis_store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisStore(client, Settings(stub_mode=True))
    yield store
    await client.aclose()


def _osv(package: str, version: str) -> list[dict]:
    if package == "urllib3":
        return [
            {
                "id": "CVE-2023-45803",
                "severity": [{"type": "CVSS_V3", "score": "7.5"}],
            }
        ]
    return []


@pytest.mark.asyncio
async def test_a2_reports_total_dependencies_separately_from_advisory_count(redis_store):
    """`requests` has no advisory in the fixture OSV response, so it must still
    count toward `total_dependencies` without appearing in `findings`."""
    agent = A2DependencyAnalyzerAgent(redis_store, Settings(stub_mode=True))
    state = RunStateModel(run_id="a2-1", repo_path=str(VULNAPI), repo_clone_path=str(VULNAPI))

    with patch("backend.agents.a2_dependency_analyzer.query_osv", side_effect=_osv):
        await agent.run(state)

    assert state.cve_report["total_dependencies"] == 2  # urllib3, requests
    assert len(state.cve_report["findings"]) == 1
    assert state.cve_report["manifest"] == "requirements.txt"
    assert state.cve_report["ecosystem"] == "PyPI"


@pytest.mark.asyncio
async def test_a2_records_the_installed_version_it_queried(redis_store):
    agent = A2DependencyAnalyzerAgent(redis_store, Settings(stub_mode=True))
    state = RunStateModel(run_id="a2-2", repo_path=str(VULNAPI), repo_clone_path=str(VULNAPI))

    with patch("backend.agents.a2_dependency_analyzer.query_osv", side_effect=_osv):
        await agent.run(state)

    record = state.cve_report["findings"][0]
    assert record["package"] == "urllib3"
    assert record["installed_version"] == "1.26.5"


@pytest.mark.asyncio
async def test_a2_records_no_reach_path_when_unreachable(redis_store):
    """The fixture repo never imports urllib3 directly — SIG-confirmed
    unreachable — so `reach_path` must be absent, not a guessed location."""
    agent = A2DependencyAnalyzerAgent(redis_store, Settings(stub_mode=True))
    state = RunStateModel(run_id="a2-3", repo_path=str(VULNAPI), repo_clone_path=str(VULNAPI))
    await redis_store.set_json(
        "a2-3",
        "sig",
        {
            "repo_path": str(VULNAPI),
            "source_roots": ["vulnapi/"],
            "files": {
                "vulnapi/api.py": {
                    "path": "vulnapi/api.py",
                    "role": "public-api",
                    "imports": ["sqlite3"],
                    "imported_by": [],
                    "churn_weight": 0.0,
                    "criticality": 0.6,
                }
            },
            "edges": [],
        },
    )

    with patch("backend.agents.a2_dependency_analyzer.query_osv", side_effect=_osv):
        await agent.run(state)

    record = state.cve_report["findings"][0]
    assert record["reachable"] is False
    assert record["reach_path"] is None
    assert record["classification"] == "Informational"


@pytest.mark.asyncio
async def test_a2_records_the_importing_files_when_reachable(redis_store):
    agent = A2DependencyAnalyzerAgent(redis_store, Settings(stub_mode=True))
    state = RunStateModel(run_id="a2-4", repo_path=str(VULNAPI), repo_clone_path=str(VULNAPI))
    await redis_store.set_json(
        "a2-4",
        "sig",
        {
            "repo_path": str(VULNAPI),
            "source_roots": ["vulnapi/"],
            "files": {
                "vulnapi/net.py": {
                    "path": "vulnapi/net.py",
                    "role": "internal-util",
                    "imports": ["urllib3"],
                    "imported_by": [],
                    "churn_weight": 0.0,
                    "criticality": 0.5,
                }
            },
            "edges": [],
        },
    )

    with patch("backend.agents.a2_dependency_analyzer.query_osv", side_effect=_osv):
        await agent.run(state)

    record = state.cve_report["findings"][0]
    assert record["reachable"] is True
    assert record["reach_path"] == ["vulnapi/net.py"]
    assert record["classification"] == "Critical"
    assert state.cve_report["critical_queue"] == ["CVE-2023-45803"]


@pytest.mark.asyncio
async def test_a2_reports_no_manifest_when_requirements_txt_is_absent(redis_store, tmp_path):
    agent = A2DependencyAnalyzerAgent(redis_store, Settings(stub_mode=True))
    state = RunStateModel(run_id="a2-5", repo_path=str(tmp_path), repo_clone_path=str(tmp_path))

    with patch(
        "backend.agents.a2_dependency_analyzer.query_osv", new_callable=AsyncMock
    ) as mock_query:
        await agent.run(state)

    mock_query.assert_not_called()
    assert state.cve_report["manifest"] is None
    assert state.cve_report["ecosystem"] is None
    assert state.cve_report["total_dependencies"] == 0
    assert state.cve_report["findings"] == []


@pytest.mark.asyncio
async def test_a2_falls_back_to_pyproject_when_requirements_txt_is_absent(redis_store, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
dependencies = [
  "urllib3==1.26.5",
  "requests>=2.31.0",
]
"""
    )
    agent = A2DependencyAnalyzerAgent(redis_store, Settings(stub_mode=True))
    state = RunStateModel(run_id="a2-6", repo_path=str(tmp_path), repo_clone_path=str(tmp_path))

    with patch("backend.agents.a2_dependency_analyzer.query_osv", side_effect=_osv):
        await agent.run(state)

    assert state.cve_report["manifest"] == "pyproject.toml"
    assert state.cve_report["ecosystem"] == "PyPI"
    assert state.cve_report["total_dependencies"] == 2
    assert len(state.cve_report["findings"]) == 1
