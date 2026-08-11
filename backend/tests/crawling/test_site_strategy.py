"""SiteFetchStrategy 测试：策略优先 / TTL 失效重探测 / 永不越权。"""
from __future__ import annotations

import pytest
from app.crawling.browser import BrowserRenderNodeExecutor
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.http_fetch import SafeFetchHttp
from app.crawling.site_strategy import SiteStrategyService
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import RobotsCache
from tests.crawling.conftest import SITE_HOST, FakeFetchTransport, make_unit, seed_ready


def _executors(ctx, transport, storage, renderer, strategy):
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    fetch = FetchNodeExecutor(
        ctx["db"],
        http=http,
        robots=robots,
        storage=storage,
        site_strategy=strategy,
        retry_base_seconds=0,
    )
    browser = BrowserRenderNodeExecutor(
        ctx["db"], renderer=renderer, robots=robots, storage=storage, site_strategy=strategy
    )
    return fetch, browser


def _dynamic_shell(slug: str) -> dict:
    return {
        "status": 200,
        "body": f'<html><body><div id="app"></div><script>x={slug}</script></body></html>'.encode(),
    }


@pytest.mark.asyncio
async def test_strategy_prefers_browser_for_second_same_site_url(ctx, storage, renderer) -> None:
    """第一次 HTTP→dynamic→Playwright 成功 → 写策略；第二个同站 URL 直接 BROWSER_PENDING。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/a": _dynamic_shell("a"),
            "/b": _dynamic_shell("b"),
        }
    )
    strategy = SiteStrategyService(db, ttl_seconds=3600)
    seed_ready(ctx, "http://fixture.test/a")
    fetch, browser = _executors(ctx, transport, storage, renderer, strategy)

    # 第一次：HTTP 得 shell → 证据 → Playwright 成功 → 记录 browser 策略
    await fetch.execute(make_unit(run, 1, "fetch"))
    await browser.execute(make_unit(run, 2, "browser_render"))
    stored = strategy.decide(user_id=user.id, site_host=SITE_HOST)
    assert stored is not None and stored.preferred_tier == "browser"

    # 第二个同站 URL：策略优先 → 直接 BROWSER_PENDING（不重复 HTTP 探测 /b）
    seed_ready(ctx, "http://fixture.test/b")
    await fetch.execute(make_unit(run, 3, "fetch"))
    assert not any("/b" in r[1] for r in transport.requests)  # HTTP 未请求 /b（策略直接走 browser）
    pending = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.BROWSER_PENDING
    )
    assert any(r.url == "http://fixture.test/b" for r in pending)
    # BrowserRender 消费并渲染
    await browser.execute(make_unit(run, 4, "browser_render"))
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 2


@pytest.mark.asyncio
async def test_strategy_ttl_expired_reprobes(ctx, storage, renderer) -> None:
    """TTL 已过 → decide None → 重新 HTTP 探测（不沿用旧策略）。"""
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/b": _dynamic_shell("b"),
        }
    )
    strategy = SiteStrategyService(db, ttl_seconds=-1)  # 过期
    strategy.record_success(
        user_id=user.id, site_host=SITE_HOST, tier="browser", tool="playwright", tool_version="1.0"
    )
    seed_ready(ctx, "http://fixture.test/b")
    fetch, _ = _executors(ctx, transport, storage, renderer, strategy)

    await fetch.execute(make_unit(run, 1, "fetch"))
    # 策略过期 → 重新探测 → HTTP 请求 /b（得到 shell → BROWSER_PENDING，而非跳过）
    assert any("/b" in r[1] for r in transport.requests)


@pytest.mark.asyncio
async def test_strategy_never_bypasses_access(ctx, storage, renderer) -> None:
    """策略存在（browser）但 URL robots/scope 拒绝 → 仍 BLOCKED（策略不绕过 AccessDecision）。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nDisallow: /private/\n"},
            "/private/x": _dynamic_shell("x"),
        }
    )
    strategy = SiteStrategyService(db, ttl_seconds=3600)
    strategy.record_success(
        user_id=user.id, site_host=SITE_HOST, tier="browser", tool="playwright", tool_version="1.0"
    )
    seed_ready(ctx, "http://fixture.test/private/x")
    fetch, _ = _executors(ctx, transport, storage, renderer, strategy)

    await fetch.execute(make_unit(run, 1, "fetch"))
    blocked = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.BLOCKED
    )
    assert len(blocked) == 1  # robots 拒绝 → BLOCKED，即使策略说 browser
