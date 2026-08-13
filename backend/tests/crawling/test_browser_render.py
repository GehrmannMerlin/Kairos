"""BrowserRenderNodeExecutor 测试：升级门禁 / HTTP 升级链保留 / 渲染失败分类。"""
from __future__ import annotations

import pytest
from app.crawling.browser import BrowserRenderNodeExecutor
from app.crawling.errors import BrowserRenderError
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.http_fetch import SafeFetchHttp
from app.crawling.repository import PageSnapshotRepository
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import RobotsCache
from app.domain.models import URLResource
from tests.crawling.conftest import (
    SITE_HOST,
    FakeFetchTransport,
    default_routes,
    make_unit,
    seed_ready,
)


def _executors(ctx, transport, storage, renderer):
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    fetch = FetchNodeExecutor(
        ctx["db"], http=http, robots=robots, storage=storage, retry_base_seconds=0
    )
    browser = BrowserRenderNodeExecutor(
        ctx["db"], renderer=renderer, robots=robots, storage=storage
    )
    return fetch, browser


@pytest.mark.asyncio
async def test_render_requires_escalation_evidence(ctx, storage, renderer) -> None:
    """无升级证据的 BROWSER_PENDING URL → 不调用 renderer，URL → FETCH_FAILED。"""
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    transport = FakeFetchTransport(default_routes())
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    browser = BrowserRenderNodeExecutor(
        ctx["db"], renderer=renderer, robots=robots, storage=storage
    )
    seed_ready(ctx, "http://fixture.test/")
    # 手动把无证据的 URL 置为 BROWSER_PENDING（模拟无证据但被标记的异常状态）
    frontier = UrlFrontierRepository(db)
    row = (
        db.query(URLResource)
        .filter(URLResource.user_id == user.id, URLResource.url == "http://fixture.test/")
        .first()
    )
    frontier.mark_state(
        user_id=user.id,
        task_id=row.task_id,
        url_hash=row.url_hash,
        state=FrontierState.BROWSER_PENDING,
    )

    result = await browser.execute(make_unit(run, 1, "browser_render"))

    assert result.status == "OK"
    assert result.committed_refs["rendered"] == 0
    assert renderer.invocation_count == 0  # 无证据不启动 Playwright
    failed = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.FETCH_FAILED
    )
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_render_preserves_http_attempt_chain(ctx, storage, renderer) -> None:
    """HTTP shell 证据 → render 后新 snapshot prior 指向 shell；两条 observation 都保留。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/dynamic": {
                "status": 200,
                "body": (
                    b'<html><body><div id="app"></div>'
                    b"<script>window.x=1</script></body></html>"
                ),
            },
        }
    )
    seed_ready(ctx, "http://fixture.test/dynamic")
    fetch, browser = _executors(ctx, transport, storage, renderer)

    fetch_result = await fetch.execute(make_unit(run, 1, "fetch"))
    assert fetch_result.committed_refs["browser_pending"] == 1

    browser_result = await browser.execute(make_unit(run, 2, "browser_render"))
    assert browser_result.status == "OK"
    assert browser_result.committed_refs["rendered"] == 1
    assert renderer.invocation_count == 1  # 有证据 → Playwright 被调用一次

    snapshots = PageSnapshotRepository(db).list_for_task(user.id, task.id)
    assert len(snapshots) == 2  # HTTP shell + rendered，两条都保留
    shell = snapshots[0]
    rendered = snapshots[1]
    assert shell.tool == "http"
    assert shell.escalation_evidence is not None
    assert rendered.tool == "playwright"
    assert rendered.prior_snapshot_id == shell.id  # 升级链审计关系
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_render_failure_sets_fetch_failed(ctx, storage) -> None:
    """renderer 抛异常 → FETCH_FAILED，不无限 Browser retry。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]

    class FailingRenderer:
        async def render(self, **kwargs):
            raise BrowserRenderError("browser down")

    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/dynamic": {
                "status": 200,
                "body": b'<html><body><div id="app"></div></body></html>',
            },
        }
    )
    seed_ready(ctx, "http://fixture.test/dynamic")
    fetch, browser = _executors(ctx, transport, storage, FailingRenderer())
    await fetch.execute(make_unit(run, 1, "fetch"))

    result = await browser.execute(make_unit(run, 2, "browser_render"))
    assert result.status == "OK"
    assert result.committed_refs["rendered"] == 0
    assert result.committed_refs["failed"] == 1
    failed = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCH_FAILED
    )
    assert len(failed) == 1
