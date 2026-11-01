from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.services.ast_import_graph import ImportGraph, build_import_graph
from backend.services.python_ast_parser import ParsedModule
from backend.services.role_classifier import RolePrediction
from backend.services.sig_cache import (
    build_cache_payload,
    compute_repo_hash,
    deserialize_payload,
    serialize_payload,
)
from backend.state.redis_store import RedisStore


@pytest_asyncio.fixture
async def redis_store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisStore(client, Settings())
    yield store
    await client.aclose()


def test_repo_hash_content_fallback(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "auth.py").write_text("x = 1\n", encoding="utf-8")
    h1 = compute_repo_hash(tmp_path, ["pkg/"])
    (tmp_path / "pkg" / "auth.py").write_text("x = 2\n", encoding="utf-8")
    h2 = compute_repo_hash(tmp_path, ["pkg/"])
    assert h1 != h2


def test_repo_hash_git_head_and_diff(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )
    h1 = compute_repo_hash(tmp_path, [""])
    (tmp_path / "a.py").write_text("a = 2\n", encoding="utf-8")
    h2 = compute_repo_hash(tmp_path, [""])
    assert h1 != h2


def test_build_import_graph_single_parse(tmp_path: Path):
    (tmp_path / "vulnapi").mkdir()
    (tmp_path / "vulnapi" / "auth.py").write_text(
        "import json\n\ndef validate_token():\n    pass\n",
        encoding="utf-8",
    )
    with patch("backend.services.ast_import_graph.parse_python_file") as mock_parse:
        mock_parse.return_value = ParsedModule(imports=["json"], functions=["validate_token"])
        graph, parsed = build_import_graph(tmp_path, source_roots=["vulnapi/"])
        assert mock_parse.call_count == 1
        assert "vulnapi/auth.py" in graph.files
        assert "vulnapi/auth.py" in parsed


@pytest.mark.asyncio
async def test_cache_hit_stores_parsed_module(redis_store: RedisStore, tmp_path: Path):
    graph = ImportGraph(files={"a.py": ["os"]}, edges=[("a.py", "os")])
    parsed = {"a.py": ParsedModule(imports=["os"], functions=["fn"], exported_symbols=["fn"])}
    roles = {"a.py": RolePrediction(role="internal-util", confidence=0.8, role_source="ast")}
    payload = build_cache_payload(["."], graph, roles, parsed, {"a.py": []})
    repo_hash = "abc123"
    await redis_store.set_sig_cache("v1", repo_hash, serialize_payload(payload), 3600)
    raw = await redis_store.get_sig_cache("v1", repo_hash)
    loaded = deserialize_payload(raw)
    assert loaded.files["a.py"].parsed_module.functions == ["fn"]


@pytest.mark.asyncio
async def test_cache_age_in_payload():
    graph = ImportGraph(files={"a.py": []}, edges=[])
    parsed = {"a.py": ParsedModule()}
    roles = {"a.py": RolePrediction(role="internal-util", confidence=1.0, role_source="filename")}
    payload = build_cache_payload(["."], graph, roles, parsed, {"a.py": []})
    payload.cached_at = datetime.utcnow() - timedelta(hours=6)
    age = (datetime.utcnow() - payload.cached_at).total_seconds()
    assert age >= 21600 - 5
