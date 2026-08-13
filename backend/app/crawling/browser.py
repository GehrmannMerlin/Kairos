"""Playwright BrowserRender（M-10 / D-009 TIER2 / 二十七 / 二十八）。

只接受已验证 AccessDecision + 明确 EscalationEvidence（或已验证站点策略）的 URL；
无证据不得升级。BrowserRenderNodeExecutor 运行于 BROWSER resource class（M-08），
core worker 不随便启动 Chromium。Browser Agent（TIER3）本轮只保留契约常量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from app.activities.execution_seam import ExecuteUnitResult
from app.crawling.contracts import EscalationEvidence, EscalationKind, FetchResult
from app.crawling.errors import BrowserRenderError, FetchErrorCode
from app.crawling.repository import PageSnapshotRepository
from app.crawling.snapshot import PageSnapshotService
from app.discovery.access_rules import AccessDecision, decide_access
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import FrontierState
from app.discovery.robots import DEFAULT_USER_AGENT, RobotsCache
from app.discovery.ssrf import assert_safe_url
from app.domain.models import Run, URLResource
from app.domain.repository import SpecVersionRepository
from app.infra.object_storage import ObjectStorage
from app.reliability.browser_pool import BrowserProcessRegistry

# D-009 TIER3：Browser Agent 本轮只保留契约，不实现自主点击/填表/滚动/验证码。
BROWSER_AGENT_REQUIRED = "BROWSER_AGENT_REQUIRED"

# M-16：进程内 active browser 登记 + 优雅退出回收（孤儿进程兜底，§48）。
_BROWSER_REGISTRY = BrowserProcessRegistry()


async def close_all_browsers() -> int:
    """Worker 优雅退出时回收所有登记浏览器进程（孤儿兜底）。"""
    return await _BROWSER_REGISTRY.close_all()


@dataclass
class RenderedPage:
    html: bytes
    final_url: str
    title: str | None = None


class BrowserRenderer(Protocol):
    async def render(
        self,
        *,
        url: str,
        timeout_seconds: float = 60.0,
        cookies: list[dict] | None = None,
    ) -> RenderedPage: ...


class PlaywrightChromiumRenderer:
    """真实 Playwright 渲染器：每个 URL 仍先执行 SSRF 校验（四十五）。"""

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_seconds: float = 60.0,
        allow_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self._headless = headless
        self._timeout_seconds = timeout_seconds
        self._allow_hosts = allow_hosts

    async def render(
        self,
        *,
        url: str,
        timeout_seconds: float | None = None,
        cookies: list[dict] | None = None,
    ) -> RenderedPage:
        assert_safe_url(url, allow_hosts=self._allow_hosts)
        to = timeout_seconds or self._timeout_seconds
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # playwright 未安装（依赖缺失）：如实暴露，不用 fake 冒充
            raise BrowserRenderError("playwright 未安装，无法渲染") from exc
        holder = f"render:{url[:120]}"
        await _BROWSER_REGISTRY.open(holder)
        try:
            try:
                async with async_playwright() as p:
                    browser = None
                    try:
                        browser = await p.chromium.launch(headless=self._headless)
                        context = await browser.new_context()
                        if cookies:
                            # cookies 是 runtime dict 契约；add_cookies 类型为 SetCookieParam
                            await context.add_cookies(cast(Any, cookies))
                        page = await context.new_page()
                        await page.goto(url, wait_until="domcontentloaded", timeout=int(to * 1000))
                        await page.wait_for_timeout(600)  # 等待同步 JS 注入
                        html = await page.content()
                        final_url = page.url
                        title = await page.title()
                    finally:
                        # M-16：正常/超时/异常都关闭 browser 进程（进程生命周期安全，§48）
                        if browser is not None:
                            await browser.close()
            except BrowserRenderError:
                raise
            except Exception as exc:
                raise BrowserRenderError(f"浏览器渲染失败: {exc}") from exc
        finally:
            await _BROWSER_REGISTRY.close(holder)
        return RenderedPage(html=html.encode("utf-8"), final_url=final_url, title=title)


class BrowserRenderNodeExecutor:
    """M-08 BROWSER_RENDER 节点执行器：只消费 BROWSER_PENDING（有升级证据）URL。"""

    def __init__(
        self,
        db: Any,
        *,
        renderer: BrowserRenderer | None = None,
        robots: RobotsCache | None = None,
        storage: ObjectStorage | None = None,
        site_strategy=None,
        user_agent: str = DEFAULT_USER_AGENT,
        allow_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self._db = db
        self._renderer = renderer or PlaywrightChromiumRenderer(allow_hosts=allow_hosts)
        self._robots = robots
        self._storage = storage
        self._site_strategy = site_strategy
        self._user_agent = user_agent
        self._allow_hosts = allow_hosts

    def _robots_cache(self) -> RobotsCache:
        if self._robots is not None:
            return self._robots
        from app.discovery.http import DiscoveryHttp

        return RobotsCache(DiscoveryHttp(allow_hosts=self._allow_hosts))

    def _snapshot_service(self, run: Run) -> PageSnapshotService:
        storage: ObjectStorage
        if self._storage is None:
            from app.infra.deps import get_object_storage

            storage = get_object_storage()
        else:
            storage = self._storage
        return PageSnapshotService(
            self._db,
            storage,
            user_id=run.user_id,
            task_id=run.task_id,
            run_id=run.id,
            spec_version=run.spec_version,
        )

    def _site_host(self, url: str) -> str:
        from urllib.parse import urlsplit

        return (urlsplit(url).hostname or "").lower()

    def _latest_escalation(self, *, user_id: int, url_resource_id: int, host: str) -> dict | None:
        """返回升级证据：优先 HTTP shell 快照证据，其次已验证站点策略。"""
        rows = PageSnapshotRepository(self._db).find_by_url_resource(
            user_id=user_id, url_resource_id=url_resource_id
        )
        for r in reversed(rows):
            if r.escalation_evidence:
                return r.escalation_evidence
        if self._site_strategy is not None:
            strategy = self._site_strategy.decide(user_id=user_id, site_host=host)
            if strategy is not None and getattr(strategy, "preferred_tier", "") == "browser":
                return EscalationEvidence(
                    kind=EscalationKind.SITE_STRATEGY_BROWSER,
                    detail="site strategy prefers browser",
                    trigger_tool="http",
                ).model_dump(mode="json")
        return None

    async def _render_one(
        self, run: Run, spec: Any, row: URLResource, robots: RobotsCache
    ) -> FetchResult:
        policy = await robots.get(row.url)
        decision = decide_access(
            row.url, spec=spec.payload, robots_policy=policy, user_agent=self._user_agent
        )
        if decision != AccessDecision.ALLOW:
            UrlFrontierRepository(self._db).mark_fetch_outcome(
                user_id=run.user_id,
                task_id=row.task_id,
                url_hash=row.url_hash,
                state=FrontierState.BLOCKED,
                error_code=f"browser_access_{decision.value}",
            )
            return FetchResult(
                status="FAILED",
                tool="playwright",
                tool_version="1.0",
                error_code=None,
                error_summary=f"browser_access_{decision.value}",
            )

        host = self._site_host(row.url)
        evidence = self._latest_escalation(
            user_id=run.user_id, url_resource_id=row.id, host=host
        )
        # 升级门禁：无证据不得渲染（二十）
        if evidence is None:
            UrlFrontierRepository(self._db).mark_fetch_outcome(
                user_id=run.user_id,
                task_id=row.task_id,
                url_hash=row.url_hash,
                state=FrontierState.FETCH_FAILED,
                error_code=FetchErrorCode.UNSUPPORTED_RESPONSE.value,
            )
            return FetchResult(
                status="FAILED",
                tool="playwright",
                tool_version="1.0",
                error_code=FetchErrorCode.UNSUPPORTED_RESPONSE,
                error_summary="无升级证据，不启动 Playwright",
            )

        try:
            rendered = await self._renderer.render(url=row.url)
        except BrowserRenderError as exc:
            UrlFrontierRepository(self._db).mark_fetch_outcome(
                user_id=run.user_id,
                task_id=row.task_id,
                url_hash=row.url_hash,
                state=FrontierState.FETCH_FAILED,
                error_code=FetchErrorCode.INTERNAL_ERROR.value,
            )
            return FetchResult(
                status="FAILED",
                tool="playwright",
                tool_version="1.0",
                error_code=FetchErrorCode.INTERNAL_ERROR,
                error_summary=str(exc),
            )

        snapshot = self._snapshot_service(run)
        ref = await snapshot.commit_raw(
            body=rendered.html,
            url_resource_id=row.id,
            tool="playwright",
            tool_version="1.0",
            source_url=row.url,
            final_url=rendered.final_url or row.url,
            http_status=None,
            content_type="text/html",
            content_length=len(rendered.html),
            duration_ms=None,
            redirect_summary=[],
            escalation_evidence=evidence,
            credential_ref=None,
            http_metadata={},
        )
        UrlFrontierRepository(self._db).mark_fetch_outcome(
            user_id=run.user_id,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.FETCHED,
            error_code=None,
        )
        if self._site_strategy is not None:
            self._site_strategy.record_success(
                user_id=run.user_id,
                site_host=host,
                tier="browser",
                tool="playwright",
                tool_version="1.0",
                structure_fingerprint=None,
                credential_required=False,
                credential_type=None,
            )
        return FetchResult(
            status="SUCCESS",
            tool="playwright",
            tool_version="1.0",
            http_status=None,
            content_type="text/html",
            content_length=len(rendered.html),
            download_bytes=len(rendered.html),
            duration_ms=None,
            snapshot_ref=ref,
            escalation_decision=True,
            escalation_evidence=EscalationEvidence(**evidence),
            retryable=False,
        )

    async def execute(self, unit) -> ExecuteUnitResult:
        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="FAILED",
                error_code="RUN_NOT_FOUND",
                committed_refs={},
            )
        spec = SpecVersionRepository(self._db).get_version(
            run.user_id, run.task_id, run.spec_version
        )
        frontier = UrlFrontierRepository(self._db)
        pending = frontier.list_by_state(
            user_id=run.user_id, task_id=run.task_id, state=FrontierState.BROWSER_PENDING
        )
        if not pending:
            return ExecuteUnitResult(
                unit_index=unit.index, status="OK", committed_refs={"rendered": 0, "run_id": run.id}
            )
        robots = self._robots_cache()
        rendered = 0
        failed = 0
        for row in pending:
            result = await self._render_one(run, spec, row, robots)
            if result.status == "SUCCESS":
                rendered += 1
            else:
                failed += 1
            self._emit_event(run, result, row)
        self._db.commit()
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "rendered": rendered,
                "failed": failed,
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
            },
        )

    def _emit_event(self, run: Run, result: FetchResult, row: URLResource) -> None:
        from app.state.events import append_domain_event

        if result.status == "SUCCESS":
            append_domain_event(
                self._db,
                user_id=run.user_id,
                aggregate_type="task",
                aggregate_id=run.task_id,
                event_type="fetch.completed",
                aggregate_version=1,
                payload={
                    "url": row.url,
                    "url_hash": row.url_hash,
                    "tool": "playwright",
                    "status": "SUCCESS",
                },
                actor_type="system",
                run_id=run.id,
                node_run_id=None,
            )
