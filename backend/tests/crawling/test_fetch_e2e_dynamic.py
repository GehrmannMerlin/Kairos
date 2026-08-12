"""E2E Fixture 2 — 动态页面：HTTP shell → EscalationEvidence → Playwright → rendered snapshot。"""

from __future__ import annotations

import pytest
from app.crawling.browser import BrowserRenderNodeExecutor
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.http_fetch import SafeFetchHttp
from app.crawling.repository import PageSnapshotRepository
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import RobotsCache
from tests.crawling.conftest import SITE_HOST, FakeFetchTransport, make_unit, seed_ready


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
async def test_dynamic_http_shell_to_playwright_rendered(ctx, storage, renderer) -> None:
    """HTTP 得 JS shell → 保留 HTTP attempt → BROWSER_PENDING → Playwright → rendered → FETCHED。"""
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
                    b'<html><head></head><body><div id="app"></div>'
                    b'<script>document.getElementById("app").innerHTML='
                    b'"<p>JS Rendered Content</p>";</script>'
                    b"</body></html>"
                ),
            },
        }
    )
    seed_ready(ctx, "http://fixture.test/dynamic")
    fetch, browser = _executors(ctx, transport, storage, renderer)

    fetch_result = await fetch.execute(make_unit(run, 1, "fetch"))
    assert fetch_result.status == "OK"
    assert fetch_result.committed_refs["browser_pending"] == 1

    browser_result = await browser.execute(make_unit(run, 2, "browser_render"))
    assert browser_result.status == "OK"
    assert browser_result.committed_refs["rendered"] == 1
    assert renderer.invocation_count == 1  # 升级原因真实存在，Playwright 只被调用一次

    snapshots = PageSnapshotRepository(db).list_for_task(user.id, task.id)
    assert len(snapshots) == 2
    rendered = snapshots[1]
    assert rendered.tool == "playwright"
    assert rendered.escalation_evidence is not None  # 升级证据保留
    assert rendered.prior_snapshot_id == snapshots[0].id
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 1 and fetched[0].url == "http://fixture.test/dynamic"


@pytest.mark.asyncio
async def test_static_page_never_reaches_renderer(ctx, storage, renderer) -> None:
    """静态页面跨层复核：Fetch 直接 FETCHED，BrowserRender 阶段不调用 renderer。"""
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/": {"status": 200, "body": b"<html><body><p>Plain Static</p></body></html>"},
        }
    )
    seed_ready(ctx, "http://fixture.test/")
    fetch, browser = _executors(ctx, transport, storage, renderer)

    fetch_result = await fetch.execute(make_unit(run, 1, "fetch"))
    assert fetch_result.status == "OK"
    assert fetch_result.committed_refs["fetched"] == 1
    assert fetch_result.committed_refs["browser_pending"] == 0

    browser_result = await browser.execute(make_unit(run, 2, "browser_render"))
    assert browser_result.status == "OK"
    assert browser_result.committed_refs["rendered"] == 0
    assert renderer.invocation_count == 0  # 静态页面不启动 Playwright（强制门禁）


@pytest.mark.asyncio
async def test_fetch_render_if_empty_renders_directly(ctx, storage, renderer) -> None:
    """DEPLOY-GATE-3 Golden C 回归：render_if_empty=true 时，fetch 在 HTTP 空壳升级
    证据提交后由 Playwright 直接渲染（不依赖计划是否含独立 browser_render 节点）。"""
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
                    b'<html><head></head><body><div id="app"></div>'
                    b'<script>document.getElementById("app").innerHTML='
                    b'"<p>JS Rendered Content</p>";</script>'
                    b"</body></html>"
                ),
            },
        }
    )
    seed_ready(ctx, "http://fixture.test/dynamic")
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    fetch = FetchNodeExecutor(
        db, http=http, robots=robots, storage=storage, retry_base_seconds=0, renderer=renderer
    )

    result = await fetch.execute(make_unit(run, 1, "fetch", parameters={"render_if_empty": True}))
    assert result.status == "OK"
    assert result.committed_refs["fetched"] == 1
    assert result.committed_refs["browser_pending"] == 0
    assert renderer.invocation_count == 1  # 升级证据后 Playwright 只被调用一次

    snapshots = PageSnapshotRepository(db).list_for_task(user.id, task.id)
    assert len(snapshots) == 2  # HTTP shell（含升级证据）+ rendered
    rendered = snapshots[1]
    assert rendered.tool == "playwright"
    assert rendered.escalation_evidence is not None  # 升级证据在 rendered 快照保留
    assert rendered.prior_snapshot_id == snapshots[0].id
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 1 and fetched[0].url == "http://fixture.test/dynamic"
