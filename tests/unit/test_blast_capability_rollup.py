"""`api/routes/ui.py::_capability_rollup` — A0.5's capabilities narrowed to A5's scope.

Additive and best-effort, same contract as `_index_pointer`: A0.5 is an
optional layer, so its absence must read as "not available" (`None`), never as
zero capabilities, and a failure reading its cache must not take the blast
endpoint down with it.
"""

from types import SimpleNamespace

import pytest

from backend.api.routes import ui as ui_routes
from backend.models.knowledge_graph import Capability, Explanation


class _Store:
    async def get_json(self, *_a, **_k):
        return {"repository_id": "repo-1"}


def _capability(name: str, slug: str, files: list[str], confidence: float = 0.8) -> Capability:
    return Capability(
        name=name, slug=slug, files=files, confidence=confidence,
        explanation=Explanation(summary=f"evidence for {name}"),
    )


@pytest.mark.asyncio
async def test_scope_narrows_to_files_actually_in_the_blast(monkeypatch):
    async def fake_load(*_a, **_k):
        return SimpleNamespace()

    monkeypatch.setattr(ui_routes, "load_repository_intelligence", fake_load)
    monkeypatch.setattr(ui_routes, "get_knowledge_graph", lambda index: index)
    monkeypatch.setattr(
        ui_routes,
        "infer_capabilities",
        lambda graph: [_capability("Authentication", "authentication", ["app/auth.py", "app/other.py"])],
    )

    result = await ui_routes._capability_rollup(_Store(), None, "r", {"app/auth.py"})

    assert len(result) == 1
    assert result[0]["name"] == "Authentication"
    assert result[0]["filesInScope"] == ["app/auth.py"]
    assert result[0]["totalFilesInCapability"] == 2
    assert result[0]["why"] == "evidence for Authentication"


@pytest.mark.asyncio
async def test_capabilities_with_no_scope_overlap_are_omitted(monkeypatch):
    async def fake_load(*_a, **_k):
        return SimpleNamespace()

    monkeypatch.setattr(ui_routes, "load_repository_intelligence", fake_load)
    monkeypatch.setattr(ui_routes, "get_knowledge_graph", lambda index: index)
    monkeypatch.setattr(
        ui_routes, "infer_capabilities", lambda graph: [_capability("Payments", "payments", ["billing/checkout.py"])]
    )

    result = await ui_routes._capability_rollup(_Store(), None, "r", {"app/auth.py"})

    names = [c["name"] for c in result]
    assert "Payments" not in names
    assert result[-1]["name"] == "Unclassified"
    assert result[-1]["filesInScope"] == ["app/auth.py"]
    assert result[-1]["confidence"] is None


@pytest.mark.asyncio
async def test_fully_classified_scope_has_no_unclassified_bucket(monkeypatch):
    async def fake_load(*_a, **_k):
        return SimpleNamespace()

    monkeypatch.setattr(ui_routes, "load_repository_intelligence", fake_load)
    monkeypatch.setattr(ui_routes, "get_knowledge_graph", lambda index: index)
    monkeypatch.setattr(
        ui_routes, "infer_capabilities", lambda graph: [_capability("Authentication", "authentication", ["app/auth.py"])]
    )

    result = await ui_routes._capability_rollup(_Store(), None, "r", {"app/auth.py"})

    assert all(c["name"] != "Unclassified" for c in result)


@pytest.mark.asyncio
async def test_absent_index_returns_none_not_an_empty_list():
    """A0.5 disabled or not yet run — the real `_Store.get_json` returns a
    pointer with no `repository_id`, which `load_repository_intelligence`
    treats as absent."""

    class _NoIndexStore:
        async def get_json(self, *_a, **_k):
            return None

    result = await ui_routes._capability_rollup(_NoIndexStore(), None, "r", {"a.py"})
    assert result is None


@pytest.mark.asyncio
async def test_a_broken_index_read_degrades_to_none_not_a_500():
    class _BrokenStore:
        async def get_json(self, *_a, **_k):
            raise RuntimeError("redis exploded")

    result = await ui_routes._capability_rollup(_BrokenStore(), None, "r", {"a.py"})
    assert result is None


@pytest.mark.asyncio
async def test_capabilities_are_ranked_by_scope_coverage(monkeypatch):
    async def fake_load(*_a, **_k):
        return SimpleNamespace()

    monkeypatch.setattr(ui_routes, "load_repository_intelligence", fake_load)
    monkeypatch.setattr(ui_routes, "get_knowledge_graph", lambda index: index)
    monkeypatch.setattr(
        ui_routes,
        "infer_capabilities",
        lambda graph: [
            _capability("API", "api", ["a.py"]),
            _capability("Authentication", "authentication", ["b.py", "c.py"]),
        ],
    )

    result = await ui_routes._capability_rollup(_Store(), None, "r", {"a.py", "b.py", "c.py"})

    assert [c["name"] for c in result] == ["Authentication", "API"]
