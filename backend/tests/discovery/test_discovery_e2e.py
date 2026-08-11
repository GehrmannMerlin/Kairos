"""M-09 Task 8 E2E（service 级，注入 fake transport，无真实 socket）。

场景 A：SPECIFIED_SOURCE seed → AccessRulesCheck → LinkDiscovery → Frontier READY_FOR_FETCH
场景 B：EXPLORATORY → Fake SearchProvider → SourceSearch → AccessRulesCheck → LinkDiscovery
        → Frontier
场景 C：robots denied 公共 URL → JIT Approval → approve → resolve → READY_FOR_FETCH

使用 fake DiscoveryTransport 返回固定响应（http.server + httpx 在 Windows 上存在
间歇性 keep-alive 超时，属测试基础设施问题；生产代码对真实站点无碍）。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from app.activities.discovery_approval import (
    ResolveRobotsOverrideInput,
    _resolve_with_session,
)
from app.activities.execution_seam import ExecutionUnit
from app.approval.service import ApprovalService
from app.auth.repository import UserRepository
from app.discovery.access_rules import AccessRulesService
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.link_discovery import LinkDiscoveryService
from app.discovery.models import DiscoverySource, FrontierState
from app.discovery.source_search import SearchService
from app.domain.repository import RunRepository, SpecVersionRepository, TaskRepository
from app.infra.db import Base
from app.providers.search_protocol import SearchResult
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SITE = "http://127.0.0.1:1"  # 非可路由端口：SSRF 靠 allow_hosts 放行，fake transport 响应


class _FakeResp:
    def __init__(self, status_code: int, text: str, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"content-type": "text/html"}


class _FakeDiscoveryTransport:
    """按 path 返回固定站点内容（robots/sitemap/rss/index），无真实网络。"""

    def _routes(self):
        return {
            "/robots.txt": f"User-agent: *\nDisallow: /private/\nSitemap: {SITE}/sitemap.xml\n",
            "/sitemap.xml": (
                '<?xml version="1.0"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{SITE}/page1</loc></url>"
                f"<url><loc>{SITE}/page2</loc></url>"
                "</urlset>"
            ),
            "/rss.xml": (
                '<rss version="2.0"><channel>'
                f"<item><link>{SITE}/feed1</link></item>"
                "</channel></rss>"
            ),
            "/": (
                "<html><body>"
                f"<nav><a href='{SITE}/products'>P</a></nav>"
                f"<a rel='next' href='{SITE}/list?page=2'>N</a>"
                f"<a href='{SITE}/detail/1'>D</a>"
                f"<a href='https://other.com/x'>X</a>"
                "</body></html>"
            ),
            "/private/x": "<html>private</html>",
        }

    async def request(self, *, method: str, url: str, timeout_seconds: float) -> _FakeResp:
        path = urlsplit(url).path
        body = self._routes().get(path)
        if body is None:
            return _FakeResp(404, "")
        return _FakeResp(200, body)


@pytest.fixture()
def http():
    return DiscoveryHttp(transport=_FakeDiscoveryTransport(), allow_hosts=frozenset({"127.0.0.1"}))


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = UserRepository(db).create("discovery@example.com", "hash", None)
    task = TaskRepository(db).create(
        user_id=user.id, title="M-09 E2E", task_type="SPECIFIED_SOURCE"
    )
    yield {"db": db, "user": user, "task": task}
    db.close()


def _spec(task_type: str, seed_urls: list[str]) -> dict:
    return {
        "task_type": task_type,
        "goal": "discovery e2e",
        "fields": [{"name": "标题", "type": "text", "required": True}],
        "source_scope": {"mode": task_type, "seed_urls": seed_urls, "source_hints": []},
        "completion_conditions": [{"kind": "min_records", "target": 1}],
        "advanced_settings": {},
    }


def _run(db, user, task, spec_version: int):
    return RunRepository(db).create(
        user_id=user.id, task_id=task.id, spec_version=spec_version, plan_version=1
    )


def _unit(run, index: int, node_type: str, parameters: dict) -> ExecutionUnit:
    return ExecutionUnit(
        run_id=run.id,
        index=index,
        unit_type=node_type,
        input_fingerprint=f"fp-{index}",
        node_id=f"n{index}",
        node_type=node_type,
        definition_version="1.0.0",
        parameters=parameters,
    )


@pytest.mark.asyncio
async def test_scenario_a_specified_source_to_ready_for_fetch(ctx, http) -> None:
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    spec = SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=_spec("SPECIFIED_SOURCE", [f"{SITE}/"]),
    )
    run = _run(db, user, task, spec.version)

    frontier = UrlFrontierRepository(db)
    frontier.upsert_discovery(
        task_id=task.id,
        user_id=user.id,
        run_id=run.id,
        spec_version=spec.version,
        raw_url=f"{SITE}/",
        source=DiscoverySource.USER_SEED,
    )

    access = await AccessRulesService(db, http=http).execute(
        _unit(run, 1, "access_rules_check", {"respect_robots": True, "public_only": True})
    )
    assert access.status == "OK"
    discover = await LinkDiscoveryService(db, http=http).execute(
        _unit(run, 2, "link_discovery", {"max_links": 200})
    )
    assert discover.status == "OK"

    ready = {r.url for r in frontier.list_ready_for_fetch(user_id=user.id, task_id=task.id)}
    assert f"{SITE}/" in ready
    assert f"{SITE}/page1" in ready
    assert f"{SITE}/page2" in ready
    assert f"{SITE}/feed1" in ready
    assert f"{SITE}/products" in ready
    assert f"{SITE}/list?page=2" in ready
    assert f"{SITE}/detail/1" in ready
    # 跨域 other.com 不作为 Frontier 直接消费项
    assert not any("other.com" in r for r in ready)


@pytest.mark.asyncio
async def test_scenario_b_exploratory_fake_search_to_frontier(ctx, http) -> None:
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    spec = SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=_spec("EXPLORATORY", []),
    )
    run = _run(db, user, task, spec.version)

    class _FakeSearchProvider:
        provider_type = "fake_search"

        async def search(self, *, query, limit, api_key, base_url):
            return [
                SearchResult(
                    url=f"{SITE}/", title="T", snippet="s", provider="fake", rank=1, query=query
                ),
                SearchResult(
                    url=f"{SITE}/page1",
                    title="P",
                    snippet="s",
                    provider="fake",
                    rank=2,
                    query=query,
                ),
            ]

    class _Cfg:
        config_id = "cfg1"
        version = 1
        provider_type = "fake_search"
        base_url = SITE
        credential_version_id = None
        connection_status = "available"

    class _CfgRepo:
        def list_current(self, user_id):
            return [_Cfg()]

    service = SearchService(
        db,
        vault=object(),
        search_configs=_CfgRepo(),
        provider_builder=lambda t: _FakeSearchProvider(),
    )
    search_result = await service.execute(_unit(run, 1, "source_search", {"query": "电动车"}))
    assert search_result.status == "OK"
    assert search_result.committed_refs["candidate_sites"] == 1

    access = await AccessRulesService(db, http=http).execute(
        _unit(run, 2, "access_rules_check", {"respect_robots": True, "public_only": True})
    )
    assert access.status == "OK"
    await LinkDiscoveryService(db, http=http).execute(
        _unit(run, 3, "link_discovery", {"max_links": 200})
    )

    frontier = UrlFrontierRepository(db)
    ready = {r.url for r in frontier.list_ready_for_fetch(user_id=user.id, task_id=task.id)}
    assert f"{SITE}/" in ready
    assert f"{SITE}/page1" in ready
    assert f"{SITE}/feed1" in ready  # 来自站内扩展


@pytest.mark.asyncio
async def test_scenario_c_robots_denied_public_override_approval(ctx, http) -> None:
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    spec = SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=_spec("SPECIFIED_SOURCE", [f"{SITE}/private/x"]),
    )
    run = _run(db, user, task, spec.version)
    frontier = UrlFrontierRepository(db)
    h, _ = frontier.upsert_discovery(
        task_id=task.id,
        user_id=user.id,
        run_id=run.id,
        spec_version=spec.version,
        raw_url=f"{SITE}/private/x",
        source=DiscoverySource.USER_SEED,
    )

    access = await AccessRulesService(db, http=http).execute(
        _unit(run, 1, "access_rules_check", {"respect_robots": True, "public_only": True})
    )
    assert access.status == "WAITING_APPROVAL"
    approval_id = access.committed_refs["approval_id"]
    parameters = access.committed_refs["parameters"]
    waiting = frontier.list_by_state(
        user_id=user.id, task_id=task.id, state=FrontierState.WAITING_APPROVAL
    )
    assert len(waiting) == 1 and waiting[0].url_hash == h

    ApprovalService(db).approve(user_id=user.id, approval_id=approval_id)
    resolved = _resolve_with_session(
        db,
        ResolveRobotsOverrideInput(
            user_id=user.id,
            approval_id=approval_id,
            url_hash=h,
            parameters=parameters,
            decision="APPROVED",
        ),
    )
    assert resolved["ok"] is True
    ready = frontier.list_ready_for_fetch(user_id=user.id, task_id=task.id)
    assert [r.url_hash for r in ready] == [h]
