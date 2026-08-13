"""Scrapy 批量 Fetch 测试：批量成功 / 不越权 / 失败隔离 / 有界并发。"""
from __future__ import annotations

import asyncio

import pytest
from app.crawling.batch import ScrapyBatchFetcher
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.http_fetch import SafeFetchHttp
from app.crawling.repository import PageSnapshotRepository
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import RobotsCache
from tests.crawling.conftest import SITE_HOST, FakeFetchTransport, seed_ready


class ConcurrencyTransport(FakeFetchTransport):
    """记录最大并发（有界并发断言）。"""

    def __init__(self, routes, delay: float = 0.03) -> None:
        super().__init__(routes)
        self.active = 0
        self.max_active = 0
        self.delay = delay

    async def request(self, *, method: str, url: str, timeout_seconds: float, headers=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return await super().request(
                method=method, url=url, timeout_seconds=timeout_seconds, headers=headers
            )
        finally:
            self.active -= 1


def _batch(db, transport, storage, max_concurrency: int = 4):
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    executor = FetchNodeExecutor(
        db, http=http, robots=robots, storage=storage, retry_base_seconds=0
    )
    return ScrapyBatchFetcher(db, executor=executor, max_concurrency=max_concurrency)


def _ready_rows(ctx):
    db = ctx["db"]
    return ScrapyBatchFetcher.ready_urls(db, user_id=ctx["user"].id, task_id=ctx["task"].id)


@pytest.mark.asyncio
async def test_batch_fetches_multiple_static_urls(ctx, storage) -> None:
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/a": {"status": 200, "body": b"<html><p>A</p></html>"},
            "/b": {"status": 200, "body": b"<html><p>B</p></html>"},
            "/c": {"status": 200, "body": b"<html><p>C</p></html>"},
            "/d": {"status": 200, "body": b"<html><p>D</p></html>"},
            "/e": {"status": 200, "body": b"<html><p>E</p></html>"},
        }
    )
    for p in ("/a", "/b", "/c", "/d", "/e"):
        seed_ready(ctx, f"http://{SITE_HOST}{p}")

    result = await _batch(db, transport, storage).run(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        urls=_ready_rows(ctx),
    )

    assert result.fetched == 5
    assert result.failed == 0
    assert result.browser_pending == 0
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 5
    snapshots = PageSnapshotRepository(db).list_for_task(user.id, task.id)
    assert len(snapshots) == 5


@pytest.mark.asyncio
async def test_batch_does_not_bypass_robots(ctx, storage) -> None:
    """robots Disallow 路径混入批量 → BLOCKED，不抓取（Scrapy 不改变访问权限）。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nDisallow: /private/\n"},
            "/public": {"status": 200, "body": b"<html><p>public</p></html>"},
            "/private/x": {"status": 200, "body": b"<html><p>secret</p></html>"},
        }
    )
    seed_ready(ctx, "http://fixture.test/public")
    seed_ready(ctx, "http://fixture.test/private/x")

    result = await _batch(db, transport, storage).run(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        urls=_ready_rows(ctx),
    )

    # /public 允许成功；/private/x 被 robots 拦截（BLOCKED，不抓取）
    assert result.fetched == 1
    blocked = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.BLOCKED
    )
    assert len(blocked) == 1
    assert "private" in blocked[0].url


@pytest.mark.asyncio
async def test_batch_failure_isolation(ctx, storage) -> None:
    """单个 URL 失败不毒化同批：成功 URL FETCHED，失败 URL FETCH_FAILED。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/ok": {"status": 200, "body": b"<html><p>ok</p></html>"},
            "/broken": {"status": 500, "body": b"server error"},
        }
    )
    seed_ready(ctx, "http://fixture.test/ok")
    seed_ready(ctx, "http://fixture.test/broken")

    result = await _batch(db, transport, storage).run(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        urls=_ready_rows(ctx),
    )

    assert result.fetched == 1
    assert result.failed == 1
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    failed = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCH_FAILED
    )
    assert [r.url for r in fetched] == ["http://fixture.test/ok"]
    assert [r.url for r in failed] == ["http://fixture.test/broken"]


@pytest.mark.asyncio
async def test_batch_bounded_concurrency(ctx, storage) -> None:
    """有界并发：max_active ≤ max_concurrency（Scrapy 批量受控，非无限并发）。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = ConcurrencyTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/a": {"status": 200, "body": b"<html><p>A</p></html>"},
            "/b": {"status": 200, "body": b"<html><p>B</p></html>"},
            "/c": {"status": 200, "body": b"<html><p>C</p></html>"},
            "/d": {"status": 200, "body": b"<html><p>D</p></html>"},
        }
    )
    for p in ("/a", "/b", "/c", "/d"):
        seed_ready(ctx, f"http://{SITE_HOST}{p}")

    result = await _batch(db, transport, storage, max_concurrency=2).run(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        urls=_ready_rows(ctx),
    )

    assert result.fetched == 4
    assert transport.max_active <= 2
