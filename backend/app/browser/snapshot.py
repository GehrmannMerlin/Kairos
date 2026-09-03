"""Browser snapshot capture and persistence (phase4).

Captures a rendered page's clean text + final screenshot PNG from the browser
session, writes them to MinIO under the `browser/` namespace, and persists a
`PageSnapshot` row with capture_method=BROWSER and an optional parent HTTP
snapshot reference. The protocol-level `_Page` shape mirrors the Harness
`PlaywrightBrowserSession`-provided page, so tests inject an in-memory double.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from app.config import get_settings
from app.models import PageSnapshot
from app.repositories import persist_page_snapshot
from app.storage import MinioS3ObjectStore, ObjectStore, browser_snapshot_object_keys
from app.url_policy import validate_public_http_url


@dataclass(frozen=True)
class BrowserPageState:
    """Bounded, serializable state captured from a rendered page."""

    url: str
    title: str | None
    text: str
    screenshot_bytes: bytes
    content_hash: str
    text_chars: int


class _PageLike:
    """Structural subset of a Playwright page the snapshot capture needs.

    The signatures intentionally mirror the real Playwright Page (which carries
    a `timeout` parameter); this is a structural protocol, not an async-timeout
    recommendation, hence the per-line noqa for ASYNC109.
    """

    @property
    def url(self) -> str: ...

    async def inner_text(
        self, selector: str, *, timeout: float | None = None  # noqa: ASYNC109
    ) -> str: ...

    async def title(self) -> str: ...

    async def screenshot(
        self, *, full_page: bool = False, timeout: float | None = None  # noqa: ASYNC109
    ) -> bytes: ...


async def capture_page_state(page: _PageLike) -> BrowserPageState:
    """Read rendered text (across the main frame; child-frame text is handled by
    the Harness toolset, here we take the page body text) and a final screenshot.

    The clean text must come from the rendered DOM, never from the original HTTP
    HTML — that is what makes this a Browser snapshot, not a re-parse of the
    HTTP body. Screenshot is only a provenance/UI artifact; it is never sent to
    a model or pushed to Temporal history.
    """
    url = page.url or ""
    title = None
    try:
        title = await page.title()
    except Exception:  # noqa: BLE001 - a title read must not fail the capture
        title = None
    text = ""
    try:
        text = await page.inner_text("body") or ""
    except Exception:  # noqa: BLE001 - bounded text read; empty text still yields a snapshot
        text = ""
    screenshot_bytes = b""
    try:
        screenshot_bytes = await page.screenshot(full_page=True)
    except Exception:  # noqa: BLE001 - screenshot is provenance; degrade gracefully
        screenshot_bytes = b""
    clean = " ".join(text.split())
    content_hash = sha256(clean.encode("utf-8")).hexdigest()
    return BrowserPageState(
        url=url,
        title=title,
        text=clean,
        screenshot_bytes=screenshot_bytes,
        content_hash=content_hash,
        text_chars=len(clean),
    )


async def persist_browser_snapshot(
    *,
    source_id: str,
    owner_id: str,
    task_id: str,
    task_run_id: str,
    state: BrowserPageState,
    parent_snapshot_id: str | None = None,
    store: ObjectStore | None = None,
) -> PageSnapshot:
    """Write browser raw/text/screenshot objects and the PageSnapshot metadata row.

    Atomic business contract: objects first, then metadata. If the DB write fails
    after objects are stored, a content-addressed orphan blob may remain (cleanup
    is deliberately out of scope this round). Never re-parse the original HTTP
    body here — the raw stored object is exactly the rendered page bytes.
    """
    settings = get_settings()
    store = store or MinioS3ObjectStore(settings)
    canonical_url = validate_public_http_url(state.url)
    keys = browser_snapshot_object_keys(owner_id, task_id, state.content_hash)
    # Raw rendered representation: keep it faithful to what the browser shows. We
    # store the visible text as raw_format too, because the Harness toolset does
    # not guarantee a raw-HTML export; `text` is authoritative for extraction.
    raw_bytes = state.text.encode("utf-8")
    await store.put_bytes(keys.raw, raw_bytes, "text/html; charset=utf-8")
    await store.put_bytes(keys.text, raw_bytes, "text/plain; charset=utf-8")
    if state.screenshot_bytes:
        await store.put_bytes(keys.screenshot, state.screenshot_bytes, "image/png")
    return await persist_page_snapshot(
        source_id=source_id,
        owner_id=owner_id,
        task_id=task_id,
        task_run_id=task_run_id,
        url=state.url,
        canonical_url=canonical_url,
        status_code=200,
        content_type="text/html",
        title=state.title,
        content_hash=state.content_hash,
        raw_storage_key=keys.raw,
        text_storage_key=keys.text,
        bytes_read=len(raw_bytes),
        text_chars=state.text_chars,
        text_preview=state.text[:2000],
        capture_method="BROWSER",
        parent_snapshot_id=parent_snapshot_id,
        screenshot_storage_key=keys.screenshot if state.screenshot_bytes else None,
        rendered_at=datetime.now(UTC),
    )