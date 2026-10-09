from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from app.api import app
from app.collection import ValidatedEvidence, ValidatedRecord
from app.domain import (
    RecordStatus,
)
from app.repositories import (
    create_task_run,
    get_collection_source_for_run,
    get_collection_spec,
    persist_extraction_commit,
    persist_page_snapshot,
)


async def _api_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_collection_spec_api_is_owner_scoped_and_deduplicates_seed_urls() -> None:
    async with await _api_client() as client:
        created = await client.post("/api/tasks", headers={"X-Kairos-User-Id": "api-owner"})
        assert created.status_code == 201
        task_id = created.json()["task_id"]
        body = {
            "goal": "Collect titles",
            "fields": [{"name": "title", "type": "STRING", "required": True}],
            "seed_urls": ["HTTPS://Example.com/page#first", "https://example.com/page#second"],
        }

        confirmed = await client.post(
            f"/api/tasks/{task_id}/collection/spec",
            json=body,
            headers={"X-Kairos-User-Id": "api-owner"},
        )
        assert confirmed.status_code == 200
        payload = confirmed.json()
        assert payload["version"] == 1
        assert payload["mode"] == "SPECIFIED_SOURCE"
        assert [source["canonical_url"] for source in payload["sources"]] == [
            "https://example.com/page"
        ]

        fetched = await client.get(
            f"/api/tasks/{task_id}/collection/spec",
            headers={"X-Kairos-User-Id": "api-owner"},
        )
        assert fetched.status_code == 200
        assert fetched.json()["spec_version_id"] == payload["spec_version_id"]

        isolated = await client.get(
            f"/api/tasks/{task_id}/collection/spec",
            headers={"X-Kairos-User-Id": "different-owner"},
        )
        assert isolated.status_code == 404


@pytest.mark.asyncio
async def test_collection_query_apis_return_records_evidence_and_safe_snapshot_metadata() -> None:
    owner_id = "api-query-owner"
    async with await _api_client() as client:
        created = await client.post("/api/tasks", headers={"X-Kairos-User-Id": owner_id})
        task_id = created.json()["task_id"]
        confirmed = await client.post(
            f"/api/tasks/{task_id}/collection/spec",
            json={
                "goal": "Collect titles",
                "fields": [{"name": "title", "type": "STRING", "required": True}],
                "seed_urls": ["https://example.com/page"],
            },
            headers={"X-Kairos-User-Id": owner_id},
        )
        assert confirmed.status_code == 200
        spec_id = confirmed.json()["spec_version_id"]
        run_id = f"run-api-query-{uuid4().hex}"
        await create_task_run(task_id, owner_id, run_id, f"workflow-api-query-{uuid4().hex}", "collect")
        spec = await get_collection_spec(task_id, owner_id)
        assert spec is not None
        source = await get_collection_source_for_run(task_id, run_id, owner_id, spec_id, spec.seed_urls[0])
        assert source is not None
        snapshot = await persist_page_snapshot(
            source_id=source.source_id,
            owner_id=owner_id,
            task_id=task_id,
            task_run_id=run_id,
            url=source.url,
            canonical_url=source.canonical_url,
            status_code=200,
            content_type="text/html",
            title="Kairos",
            content_hash="api-query-hash",
            raw_storage_key="internal/raw.html",
            text_storage_key="internal/text.txt",
            bytes_read=100,
            text_chars=13,
            text_preview="Title: Kairos",
        )
        committed = await persist_extraction_commit(
            snapshot_id=snapshot.snapshot_id,
            task_id=task_id,
            task_run_id=run_id,
            owner_id=owner_id,
            spec_version_id=spec_id,
            payload_hash="api-query-payload",
            validated_records=[
                ValidatedRecord(
                    data_json={"title": "Kairos"},
                    status=RecordStatus.PASSED,
                    validation_issues=[],
                    evidence=[
                        ValidatedEvidence(
                            field_name="title",
                            quote="Title: Kairos",
                            confidence=0.9,
                            verified=True,
                        )
                    ],
                )
            ],
            source_complete=True,
        )

        progress = await client.get(
            f"/api/tasks/{task_id}/collection/progress",
            headers={"X-Kairos-User-Id": owner_id},
        )
        assert progress.status_code == 200
        assert progress.json()["processed_sources"] == 1
        assert progress.json()["remaining_sources"] == 0

        records = await client.get(
            f"/api/tasks/{task_id}/records",
            headers={"X-Kairos-User-Id": owner_id},
        )
        assert records.status_code == 200
        assert records.json()[0]["record_id"] == committed.record_ids[0]
        assert records.json()[0]["fields"] == {"title": "Kairos"}

        evidence = await client.get(
            f"/api/tasks/{task_id}/records/{committed.record_ids[0]}/evidence",
            headers={"X-Kairos-User-Id": owner_id},
        )
        assert evidence.status_code == 200
        assert evidence.json()[0]["value"] == "Kairos"
        assert evidence.json()[0]["verified"] is True
        assert evidence.json()[0]["source_url"] == "https://example.com/page"

        metadata = await client.get(
            f"/api/tasks/{task_id}/snapshots/{snapshot.snapshot_id}",
            headers={"X-Kairos-User-Id": owner_id},
        )
        assert metadata.status_code == 200
        assert metadata.json()["snapshot_id"] == snapshot.snapshot_id
        assert "raw_storage_key" not in metadata.json()
        assert "text_storage_key" not in metadata.json()

        isolated = await client.get(
            f"/api/tasks/{task_id}/records/{committed.record_ids[0]}/evidence",
            headers={"X-Kairos-User-Id": "different-owner"},
        )
        assert isolated.status_code == 404
