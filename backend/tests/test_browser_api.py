"""Phase 4 browser API tests: owner-scoped screenshot + browser task status."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from app.api import app
from app.browser.repository import claim_browser_task, complete_browser_task
from app.db import SessionFactory
from app.domain import (
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionSpecConfirm,
)
from app.models import CollectionSource, PageSnapshot
from app.repositories import confirm_collection_spec, create_task_run, insert_task
from sqlalchemy import select


async def _client() -> httpx.AsyncClient:
    from httpx import ASGITransport

    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_browser_task_and_snapshot() -> tuple[str, str, str, str]:
    """Create task + spec + run + source + browser task + completed snapshot."""
    from app.config import get_settings
    from app.storage import MinioS3ObjectStore

    owner = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner)
    spec = await confirm_collection_spec(
        task_id,
        owner,
        CollectionSpecConfirm(
            goal="collect",
            fields=[CollectionFieldSpec(name="quote", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://quotes.toscrape.com/js/"],
        ),
    )
    await create_task_run(task_id, owner, run_id, f"wf-{uuid4().hex}", "go")
    async with SessionFactory() as db:
        src = await db.scalar(
            select(CollectionSource).where(CollectionSource.task_id == task_id)
        )
        assert src is not None
        source_id = src.source_id

    claimed = await claim_browser_task(
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner,
        spec_version_id=spec.spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert claimed is not None

    settings = get_settings()
    store = MinioS3ObjectStore(settings)
    screenshot_key = f"browser/{owner}/{task_id}/shot/screenshot.png"
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
    )
    await store.put_bytes(screenshot_key, png_bytes, "image/png")

    snapshot_id = f"snapshot-{uuid4().hex}"
    async with SessionFactory() as db:
        db.add(
            PageSnapshot(
                snapshot_id=snapshot_id,
                owner_id=owner,
                task_id=task_id,
                task_run_id=run_id,
                source_id=source_id,
                url="https://quotes.toscrape.com/js/",
                canonical_url="https://quotes.toscrape.com/js/",
                status_code=200,
                content_type="text/html",
                title="Quotes JS",
                content_hash=f"hash-{uuid4().hex}",
                raw_storage_key="browser/raw/rendered.html",
                text_storage_key="browser/text/text.txt",
                bytes_read=10,
                text_chars=10,
                text_preview="quotes",
                capture_method="BROWSER",
                screenshot_storage_key=screenshot_key,
                rendered_at=None,
            )
        )
        await db.commit()
    await complete_browser_task(
        browser_task_id=claimed.browser_task_id,
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner,
        snapshot_id=snapshot_id,
        source_id=source_id,
    )
    return owner, task_id, run_id, snapshot_id


@pytest.mark.asyncio
async def test_snapshot_metadata_browser_fields() -> None:
    owner, task_id, _, snapshot_id = await _seed_browser_task_and_snapshot()
    async with await _client() as client:
        resp = await client.get(
            f"/api/tasks/{task_id}/snapshots/{snapshot_id}",
            headers={"X-Kairos-User-Id": owner},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["capture_method"] == "BROWSER"
    assert body["has_screenshot"] is True
    assert body["parent_snapshot_id"] is None
    assert "screenshot_storage_key" not in body


@pytest.mark.asyncio
async def test_screenshot_owner_scoped_and_png() -> None:
    owner, task_id, _, snapshot_id = await _seed_browser_task_and_snapshot()
    async with await _client() as client:
        resp = await client.get(
            f"/api/tasks/{task_id}/snapshots/{snapshot_id}/screenshot",
            headers={"X-Kairos-User-Id": owner},
        )
        resp_other = await client.get(
            f"/api/tasks/{task_id}/snapshots/{snapshot_id}/screenshot",
            headers={"X-Kairos-User-Id": "other-owner"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert len(resp.content) > 0
    assert resp_other.status_code == 404


@pytest.mark.asyncio
async def test_browser_tasks_list_owner_scoped() -> None:
    owner, task_id, _, snapshot_id = await _seed_browser_task_and_snapshot()
    async with await _client() as client:
        resp = await client.get(f"/api/tasks/{task_id}/browser-tasks", headers={"X-Kairos-User-Id": owner})
        resp_other = await client.get(
            f"/api/tasks/{task_id}/browser-tasks", headers={"X-Kairos-User-Id": "other-owner"}
        )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "COMPLETED"
    assert rows[0]["snapshot_id"] == snapshot_id
    assert "screenshot" not in rows[0]
    assert resp_other.status_code == 404