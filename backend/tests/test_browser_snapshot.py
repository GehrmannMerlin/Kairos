"""Phase 4 browser snapshot capture + persistence tests (in-memory store)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.browser.snapshot import BrowserPageState, capture_page_state, persist_browser_snapshot
from app.domain import CollectionFieldSpec, CollectionFieldType, CollectionSpecConfirm
from app.repositories import confirm_collection_spec, insert_task
from app.storage import InMemoryObjectStore, browser_snapshot_object_keys


class FakePage:
    """Structural double matching the Harness session page surface."""

    def __init__(self, url: str, title: str, body: str, screenshot: bytes = b"png-bytes") -> None:
        self.url = url
        self._title = title
        self._body = body
        self._screenshot = screenshot

    async def inner_text(self, selector: str, *, timeout: float | None = None) -> str:  # noqa: ASYNC109
        return self._body

    async def title(self) -> str:
        return self._title

    async def screenshot(
        self, *, full_page: bool = False, timeout: float | None = None  # noqa: ASYNC109
    ) -> bytes:
        return self._screenshot


async def _seed_source() -> tuple[str, str, str, str, str]:
    owner = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner)
    spec = await confirm_collection_spec(
        task_id,
        owner,
        CollectionSpecConfirm(
            goal="collect quotes",
            fields=[CollectionFieldSpec(name="quote", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://quotes.toscrape.com/js/"],
        ),
    )
    from app.db import SessionFactory
    from app.models import CollectionSource
    from app.repositories import create_task_run
    from sqlalchemy import select

    created = await create_task_run(task_id, owner, run_id, f"wf-{uuid4().hex}", "go")
    assert created is not None
    async with SessionFactory() as db:
        src = await db.scalar(
            select(CollectionSource).where(CollectionSource.task_id == task_id)
        )
        assert src is not None
    return owner, task_id, run_id, spec.spec_version_id, src.source_id


@pytest.mark.asyncio
async def test_browser_snapshot_object_keys_namespace() -> None:
    keys = browser_snapshot_object_keys("owner1", "task1", "abc123")
    assert keys.raw.startswith("browser/owner1/task1/abc123/")
    assert keys.text.endswith("text.txt")
    assert keys.screenshot is not None
    assert keys.screenshot.endswith("screenshot.png")
    assert "snapshots/" not in keys.raw


@pytest.mark.asyncio
async def test_capture_page_state_uses_rendered_text() -> None:
    page = FakePage(
        url="https://quotes.toscrape.com/js/",
        title="Quotes to Scrape (JS)",
        body="  “The world as we have created it” — Albert Einstein   \n Another quote  ",
    )
    state = await capture_page_state(page)
    assert isinstance(state, BrowserPageState)
    assert state.title == "Quotes to Scrape (JS)"
    assert "Albert Einstein" in state.text
    # Cleaned: no double-space and quotes preserved.
    assert state.text == state.text.strip()
    assert state.text_chars == len(state.text)
    assert state.content_hash
    assert len(state.screenshot_bytes) == len(b"png-bytes")


@pytest.mark.asyncio
async def test_capture_page_state_degrades_on_missing_screenshot() -> None:
    class NoShotPage(FakePage):
        async def screenshot(
            self, *, full_page: bool = False, timeout: float | None = None  # noqa: ASYNC109
        ) -> bytes:
            raise RuntimeError("screenshot unsupported")

    page = NoShotPage("https://quotes.toscrape.com/js/", "Q", "   Hello rendered   ")
    state = await capture_page_state(page)
    assert state.text == "Hello rendered"
    assert state.screenshot_bytes == b""
    assert state.text_chars == len("Hello rendered")


@pytest.mark.asyncio
async def test_persist_browser_snapshot_writes_minio_and_row() -> None:
    owner, task_id, run_id, spec_version_id, source_id = await _seed_source()
    store = InMemoryObjectStore()
    state = BrowserPageState(
        url="https://quotes.toscrape.com/js/",
        title="Quotes JS",
        text="“A good friend” — Steve Martin",
        screenshot_bytes=b"png-bytes",
        content_hash="browser-hash",
        text_chars=len("“A good friend” — Steve Martin"),
    )
    snap = await persist_browser_snapshot(
        source_id=source_id,
        owner_id=owner,
        task_id=task_id,
        task_run_id=run_id,
        state=state,
        store=store,
    )
    assert snap.capture_method == "BROWSER"
    assert snap.text_chars == state.text_chars
    assert snap.screenshot_storage_key is not None
    assert snap.rendered_at is not None
    # MinIO got raw + text + screenshot.
    keys = browser_snapshot_object_keys(owner, task_id, "browser-hash")
    assert keys.raw in store.objects
    assert keys.text in store.objects
    assert keys.screenshot in store.objects
    assert store.objects[keys.screenshot] == b"png-bytes"


@pytest.mark.asyncio
async def test_persist_browser_snapshot_no_screenshot_no_key() -> None:
    owner, task_id, run_id, spec_version_id, source_id = await _seed_source()
    store = InMemoryObjectStore()
    state = BrowserPageState(
        url="https://quotes.toscrape.com/js/",
        title=None,
        text="only text",
        screenshot_bytes=b"",
        content_hash="browser-hash-2",
        text_chars=9,
    )
    snap = await persist_browser_snapshot(
        source_id=source_id,
        owner_id=owner,
        task_id=task_id,
        task_run_id=run_id,
        state=state,
        store=store,
    )
    assert snap.screenshot_storage_key is None
    assert snap.capture_method == "BROWSER"
    assert snap.parent_snapshot_id is None