"""E2E Fixture 3 — 网站凭据：401 → Credential Required → Drawer → Approval → 带凭据抓取。"""
from __future__ import annotations

import pytest
from app.activities.credential_approval import ResolveCredentialAccessInput, _resolve_with_session
from app.approval.schemas import ApprovalScope
from app.approval.service import ApprovalService
from app.crawling.credentials import WebsiteCredentialService
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.http_fetch import SafeFetchHttp
from app.crawling.repository import PageSnapshotRepository
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import RobotsCache
from tests.crawling.conftest import SITE_HOST, FakeFetchTransport, make_unit, seed_ready

SECRET_COOKIE = "SUPERSECRETCOOKIEVALUE_ABC123"


@pytest.fixture()
def vault(ctx):
    return CredentialVault(
        master_key=b"\x00" * 32,
        key_version="test",
        repository=CredentialRepository(ctx["db"]),
    )


def _executor(ctx, transport, storage, resolver=None):
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    return FetchNodeExecutor(
        ctx["db"],
        http=http,
        robots=robots,
        storage=storage,
        credential_resolver=resolver,
        retry_base_seconds=0,
    )


def _protected_route():
    def protected(headers):
        if headers.get("cookie") and SECRET_COOKIE in headers["cookie"]:
            return {"status": 200, "body": b"<html><body><p>Protected Content</p></body></html>"}
        return {"status": 401, "body": b"login required"}

    return {
        "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
        "/protected": protected,
    }


@pytest.mark.asyncio
async def test_credential_cookie_e2e(ctx, storage, vault) -> None:
    """完整链：401 → CREDENTIAL_REQUIRED → 存凭据 → Approval → resolve → 带 Cookie 抓取。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(_protected_route())
    seed_ready(ctx, "http://fixture.test/protected")

    # 1. 无凭据 Fetch → CREDENTIAL_REQUIRED，URL → WAITING_CREDENTIAL
    result = await _executor(ctx, transport, storage).execute(make_unit(run, 1, "fetch"))
    assert result.status == "CREDENTIAL_REQUIRED"
    waiting = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.WAITING_CREDENTIAL
    )
    assert len(waiting) == 1

    # 2. Credential Drawer 存储 → 创建 credential_access Approval
    service = WebsiteCredentialService(db, vault)
    meta = service.store(
        user_id=user.id,
        task_id=task.id,
        ctype="cookie",
        payload={
            "cookies": [{"name": "session", "value": SECRET_COOKIE, "domain": "fixture.test"}]
        },
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    approval = ApprovalService(db).request_approval(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        plan_version=1,
        node_id=None,
        node_type="fetch",
        action_type="credential_access",
        target="fixture.test",
        parameters={"task_id": task.id, "domain": "fixture.test", "type": "cookie"},
        scope=ApprovalScope.THIS_ACTION,
        credential_ref=meta,
    )

    # 3. 用户批准 → resolve（consume 复验 + WAITING_CREDENTIAL → READY_FOR_FETCH）
    ApprovalService(db).approve(user_id=user.id, approval_id=approval.id)
    resolved = _resolve_with_session(
        db,
        ResolveCredentialAccessInput(
            user_id=user.id,
            approval_id=approval.id,
            url_hash=waiting[0].url_hash,
            parameters={"task_id": task.id, "domain": "fixture.test", "type": "cookie"},
            decision="APPROVED",
        ),
    )
    assert resolved["ok"] is True
    ready = UrlFrontierRepository(db).list_ready_for_fetch(user_id=user.id, task_id=task.id)
    assert len(ready) == 1

    # 4. Fetch 重跑（带 resolver）→ 凭据附着 → 200 → PageSnapshot → FETCHED
    resolver = WebsiteCredentialService(db, vault)
    result2 = await _executor(ctx, transport, storage, resolver=resolver).execute(
        make_unit(run, 1, "fetch")
    )
    assert result2.status == "OK"
    assert result2.committed_refs["fetched"] == 1
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 1
    snapshots = PageSnapshotRepository(db).list_for_task(user.id, task.id)
    assert len(snapshots) == 1
    # 快照 metadata 无 secret 明文
    assert SECRET_COOKIE not in str(snapshots[0].http_metadata or {})
    assert SECRET_COOKIE not in str(snapshots[0].credential_ref or {})


@pytest.mark.asyncio
async def test_credential_username_password_contract(ctx, storage, vault) -> None:
    """Username/Password 凭据经 HTTP Basic 附着抓取受保护端点。"""
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]

    def basic(headers):
        expected = "Basic a2Fpcm9zX3VzZXI6cGFzc3dvcmQ="  # kairos_user:password
        if headers.get("authorization") == expected:
            return {"status": 200, "body": b"<html><body><p>Basic Protected</p></body></html>"}
        return {"status": 401, "body": b"auth required"}

    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/basic": basic,
        }
    )
    service = WebsiteCredentialService(db, vault)
    service.store(
        user_id=user.id,
        task_id=task.id,
        ctype="username_password",
        payload={"username": "kairos_user", "password": "password"},
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    seed_ready(ctx, "http://fixture.test/basic")
    result = await _executor(ctx, transport, storage, resolver=service).execute(
        make_unit(run, 1, "fetch")
    )
    assert result.status == "OK"
    assert result.committed_refs["fetched"] == 1
    fetched = UrlFrontierRepository(db).list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.FETCHED
    )
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_cross_domain_cookie_not_leaked(ctx, vault) -> None:
    """cookie domain=fixture.test 附着到 other.com URL → build_headers 返回 None（四十一）。"""
    db = ctx["db"]
    service = WebsiteCredentialService(db, vault)
    meta = service.store(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        ctype="cookie",
        payload={
            "cookies": [{"name": "session", "value": SECRET_COOKIE, "domain": "fixture.test"}]
        },
        scope="CURRENT_TASK",
        domain="fixture.test",
    )
    headers = service.build_headers(
        user_id=ctx["user"].id,
        credential_ref={"credential_id": meta["credential_id"], "type": "cookie"},
        url="http://other.example/",
    )
    assert headers is None  # a.com 凭据绝不发送给 b.com
