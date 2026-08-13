"""E2E Fixture 1 — 静态 HTML 全链（强制：静态页面不启动 Playwright）。

READY_FOR_FETCH → FetchNodeExecutor → SafeFetchHttp → PageSnapshot → ObjectStorage → FETCHED。
"""

from __future__ import annotations

import hashlib

import pytest
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.repository import PageSnapshotRepository
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import FrontierState
from tests.crawling.conftest import make_unit, seed_ready


@pytest.mark.asyncio
async def test_static_fetch_full_chain_and_no_browser(ctx, http, robots, storage) -> None:
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    body = b"<html><body><p>Hello Static World</p></body></html>"
    seed_ready(ctx, "http://fixture.test/")

    executor = FetchNodeExecutor(
        db, http=http, robots=robots, storage=storage, retry_base_seconds=0
    )
    result = await executor.execute(make_unit(run, 1, "fetch"))

    assert result.status == "OK"
    assert result.committed_refs["fetched"] == 1
    assert result.committed_refs["browser_pending"] == 0

    frontier = UrlFrontierRepository(db)
    rows = frontier.list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.FETCHED
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.url == "http://fixture.test/"
    # snapshot 落库 + content hash 与正文 sha256 一致
    snapshots = PageSnapshotRepository(db).list_for_task(user.id, ctx["task"].id)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.content_hash == hashlib.sha256(body).hexdigest()
    assert snap.storage_ref  # ObjectStorage ref 非空
    assert snap.tool == "http"
    assert snap.http_status == 200
    # 静态页面：无升级证据、无 BROWSER_PENDING
    assert snap.escalation_evidence is None
    assert storage.put_calls == 1


@pytest.mark.asyncio
async def test_structured_fetch_saves_raw_content(ctx, storage) -> None:
    """结构化响应（RSS）经同一安全 HTTP 层保存原始内容，不启动第二套爬虫。"""
    from app.crawling.http_fetch import SafeFetchHttp
    from app.discovery.http import DiscoveryHttp
    from app.discovery.robots import RobotsCache
    from tests.crawling.conftest import SITE_HOST, FakeFetchTransport

    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    rss_body = (
        b'<?xml version="1.0"?><rss version="2.0"><channel>'
        b"<item><link>http://fixture.test/item1</link></item></channel></rss>"
    )
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/feed.xml": {"status": 200, "content_type": "application/rss+xml", "body": rss_body},
        }
    )
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    seed_ready(ctx, "http://fixture.test/feed.xml")

    executor = FetchNodeExecutor(
        db, http=http, robots=robots, storage=storage, retry_base_seconds=0
    )
    result = await executor.execute(make_unit(run, 1, "fetch"))

    assert result.status == "OK"
    assert result.committed_refs["fetched"] == 1
    snapshots = PageSnapshotRepository(db).list_for_task(user.id, ctx["task"].id)
    assert len(snapshots) == 1
    assert snapshots[0].mime_type == "application/rss+xml"
    assert snapshots[0].content_hash == hashlib.sha256(rss_body).hexdigest()
