"""E2E Fixture 4 — 失败/重试：有界重试最终成功；401/403 不盲升级；captcha 不自动绕过。"""
from __future__ import annotations

import pytest
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.http_fetch import SafeFetchHttp
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import RobotsCache
from tests.crawling.conftest import SITE_HOST, FakeFetchTransport, make_unit, seed_ready


def _executor(ctx, transport, storage):
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    return FetchNodeExecutor(
        ctx["db"], http=http, robots=robots, storage=storage, retry_base_seconds=0
    )


@pytest.mark.asyncio
async def test_failure_then_success_bounded_retry(ctx, storage) -> None:
    """第一次 503、第二次 200 → 有界重试最终成功；不是无限重试。"""
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    state = {"n": 0}

    def flaky(headers):
        state["n"] += 1
        if state["n"] == 1:
            return {"status": 503, "body": b"server busy"}
        return {"status": 200, "body": b"<html><body><p>Recovered</p></body></html>"}

    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/flaky": flaky,
        }
    )
    seed_ready(ctx, "http://fixture.test/flaky")
    executor = _executor(ctx, transport, storage)

    result = await executor.execute(make_unit(run, 1, "fetch"))

    assert result.status == "OK"
    assert result.committed_refs["fetched"] == 1
    # 有界重试：只请求了 2 次（1 次 503 + 1 次 200），没有无限循环
    flaky_calls = [r for r in transport.requests if "/flaky" in r[1]]
    assert len(flaky_calls) == 2
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_401_leads_to_credential_required_no_escalation(ctx, storage) -> None:
    """401 → CREDENTIAL_REQUIRED（无凭据），URL → WAITING_CREDENTIAL；不触发 Playwright。"""
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/login": {"status": 401, "body": b"login required"},
        }
    )
    seed_ready(ctx, "http://fixture.test/login")
    executor = _executor(ctx, transport, storage)

    result = await executor.execute(make_unit(run, 1, "fetch"))

    assert result.status == "CREDENTIAL_REQUIRED"
    waiting = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.WAITING_CREDENTIAL
    )
    assert len(waiting) == 1 and waiting[0].url == "http://fixture.test/login"
    browser_pending = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.BROWSER_PENDING
    )
    assert len(browser_pending) == 0


@pytest.mark.asyncio
async def test_403_access_denied_no_escalation(ctx, storage) -> None:
    """403 → ACCESS_DENIED（FETCH_FAILED）；不把 403 当 Playwright 升级理由。"""
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/denied": {"status": 403, "body": b"forbidden"},
        }
    )
    seed_ready(ctx, "http://fixture.test/denied")
    executor = _executor(ctx, transport, storage)

    result = await executor.execute(make_unit(run, 1, "fetch"))

    assert result.status == "OK"
    failed = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.FETCH_FAILED
    )
    assert len(failed) == 1
    assert failed[0].fetch_error_code == "ACCESS_DENIED"
    browser_pending = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.BROWSER_PENDING
    )
    assert len(browser_pending) == 0


@pytest.mark.asyncio
async def test_captcha_no_bypass(ctx, storage) -> None:
    """captcha marker → CAPTCHA_REQUIRED；无 auto-solve / 第三方 bypass / 无限 Browser retry。"""
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/captcha": {"status": 200, "body": b"<html><body>solve the captcha</body></html>"},
        }
    )
    seed_ready(ctx, "http://fixture.test/captcha")
    executor = _executor(ctx, transport, storage)

    result = await executor.execute(make_unit(run, 1, "fetch"))

    assert result.status == "OK"
    failed = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=ctx["task"].id, state=FrontierState.FETCH_FAILED
    )
    assert len(failed) == 1
    assert failed[0].fetch_error_code == "CAPTCHA_REQUIRED"
    # 只有 1 次 captcha 请求：无自动绕过重试
    captcha_calls = [r for r in transport.requests if "/captcha" in r[1]]
    assert len(captcha_calls) == 1
