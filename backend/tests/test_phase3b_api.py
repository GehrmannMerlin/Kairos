from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from app.api import app
from app.domain import CollectionFieldSpec, CollectionFieldType, CollectionSpecConfirm
from app.repositories import (
    confirm_collection_spec,
    create_task_run,
    get_collection_spec,
    insert_task,
)
from app.search import SearchProviderResponse


class _ConfiguredFakeProvider:
    provider_name = "fake"

    async def search(self, *, query: str, max_results: int) -> SearchProviderResponse:
        return SearchProviderResponse(provider=self.provider_name, query=query, results=[])


async def _api_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_exploratory_spec_can_be_created_without_seed_urls_when_provider_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.resolve_search_provider", lambda config_id=None: _ConfiguredFakeProvider())
    async with await _api_client() as client:
        created = await client.post("/api/tasks", headers={"X-Kairos-User-Id": "phase3b-owner"})
        task_id = created.json()["task_id"]
        confirmed = await client.post(
            f"/api/tasks/{task_id}/collection/spec",
            headers={"X-Kairos-User-Id": "phase3b-owner"},
            json={
                "goal": "Find projects",
                "fields": [{"name": "name", "type": "STRING", "required": True}],
                "seed_urls": [],
                "target_count": 5,
                "mode": "EXPLORATORY",
            },
        )

    assert confirmed.status_code == 200
    assert confirmed.json()["mode"] == "EXPLORATORY"
    assert confirmed.json()["target_count"] == 5
    assert confirmed.json()["sources"] == []


@pytest.mark.asyncio
async def test_exploratory_spec_is_rejected_without_search_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.search import SearchProviderError

    def missing_provider(config_id=None):
        raise SearchProviderError("SEARCH_PROVIDER_CREDENTIAL_REQUIRED", "search provider is required")

    monkeypatch.setattr("app.api.resolve_search_provider", missing_provider)
    async with await _api_client() as client:
        created = await client.post("/api/tasks", headers={"X-Kairos-User-Id": "phase3b-owner-missing"})
        task_id = created.json()["task_id"]
        confirmed = await client.post(
            f"/api/tasks/{task_id}/collection/spec",
            headers={"X-Kairos-User-Id": "phase3b-owner-missing"},
            json={
                "goal": "Find projects",
                "fields": [{"name": "name", "type": "STRING", "required": True}],
                "target_count": 5,
                "mode": "EXPLORATORY",
            },
        )

    assert confirmed.status_code == 400
    assert confirmed.json()["detail"]["code"] == "SEARCH_PROVIDER_CREDENTIAL_REQUIRED"


@pytest.mark.asyncio
async def test_search_rounds_are_owner_scoped_and_progress_exposes_phase3b_fields() -> None:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="Find projects",
            fields=[CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True)],
            seed_urls=[],
            target_count=5,
            mode="EXPLORATORY",
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "find projects")
    async with await _api_client() as client:
        progress = await client.get(
            f"/api/tasks/{task_id}/collection/progress",
            headers={"X-Kairos-User-Id": owner_id},
        )
        rounds = await client.get(
            f"/api/tasks/{task_id}/collection/search-rounds",
            headers={"X-Kairos-User-Id": owner_id},
        )
        isolated = await client.get(
            f"/api/tasks/{task_id}/collection/search-rounds",
            headers={"X-Kairos-User-Id": "other-owner"},
        )

    assert progress.status_code == 200
    assert progress.json()["mode"] == "EXPLORATORY"
    assert progress.json()["target_count"] == 5
    assert progress.json()["passed_canonical_records"] == 0
    assert progress.json()["saturation_state"] == "NOT_REACHED"
    assert rounds.status_code == 200
    assert rounds.json() == []
    assert isolated.status_code == 404
