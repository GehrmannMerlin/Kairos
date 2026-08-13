"""M-08 FETCH 节点真实执行器（M-10 / D-009 静态层）。

只消费 READY_FOR_FETCH 且 AccessDecision=ALLOW 的 URL；复用 M-09 AccessDecision/
robots/SSRF/错误分类。Scrapy 批量（Task 3）与本站点策略（Task 6）都走同一安全路径。
升级到 Playwright 只发生在 EMPTY_BODY / DYNAMIC_APP_SHELL（或已验证站点策略）证据下；
401/403/captcha 不是升级理由。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.activities.execution_seam import ExecuteUnitResult
from app.crawling.content import (
    build_escalation_evidence,
    classify_content,
    contains_captcha,
)
from app.crawling.contracts import (
    EscalationEvidence,
    EscalationKind,
    FetchResult,
)
from app.crawling.errors import FetchErrorCode, HttpFetchError
from app.crawling.http_fetch import SafeFetchHttp, map_transport_error
from app.crawling.snapshot import PageSnapshotService
from app.discovery.access_rules import AccessDecision, decide_access
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import DEFAULT_USER_AGENT, RobotsCache
from app.domain.models import Run, URLResource
from app.domain.repository import SpecVersionRepository
from app.infra.object_storage import ObjectStorage


class CredentialResolver(Protocol):
    """凭据解析/附着（Task 5 实现）。只暴露脱敏 ref；明文仅在 build_headers 内瞬态使用。"""

    def resolve_ref(self, *, user_id: int, task_id: int, domain: str) -> dict | None: ...

    def build_headers(
        self, *, user_id: int, credential_ref: dict, url: str
    ) -> dict | None: ...


class _DiscoveryFromFetchTransport:
    """把 FetchTransport 适配为 DiscoveryHttp 需要的 DiscoveryTransport（同一底层连接）。"""

    def __init__(self, fetch_transport: Any) -> None:
        self._t = fetch_transport

    async def request(self, *, method: str, url: str, timeout_seconds: float):
        from app.discovery.http import _HttpResponse

        raw = await self._t.request(
            method=method, url=url, timeout_seconds=timeout_seconds, headers=None
        )
        return _HttpResponse(
            status_code=raw.status_code,
            headers=raw.headers,
            text=raw.content.decode("utf-8", errors="ignore"),
        )


def _retry_after_from_body(body: Any) -> float | None:
    """解析 Retry-After 响应头（整数/秒）；不存在或不可解析返回 None。"""
    try:
        headers = getattr(body, "headers", None) or {}
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw is None:
            return None
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError, AttributeError):
        return None


class FetchNodeExecutor:
    """Fetch 执行器：对 READY_FOR_FETCH 批次做静态层抓取 + 证据升级标记。"""

    def __init__(
        self,
        db: Any,
        *,
        http: SafeFetchHttp | None = None,
        robots: RobotsCache | None = None,
        storage: ObjectStorage | None = None,
        site_strategy=None,
        credential_resolver: CredentialResolver | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        allow_hosts: frozenset[str] = frozenset(),
        max_internal_retries: int = 2,
        retry_base_seconds: float = 1.0,
        max_batch: int = 100,
    ) -> None:
        self._db = db
        self._http = http or SafeFetchHttp(allow_hosts=allow_hosts)
        self._robots = robots
        self._storage = storage
        self._site_strategy = site_strategy
        self._credential_resolver = credential_resolver
        self._user_agent = user_agent
        self._allow_hosts = allow_hosts
        self._max_internal_retries = max_internal_retries
        self._retry_base_seconds = retry_base_seconds
        self._max_batch = max_batch

    def robots_cache(self) -> RobotsCache:
        if self._robots is not None:
            return self._robots
        # 复用同一底层 transport 的 DiscoveryHttp（M-09 robots 语义），不新建第二套 client
        discovery = DiscoveryHttp(
            transport=_DiscoveryFromFetchTransport(self._http._transport),
            allow_hosts=self._http._allow_hosts,
        )
        return RobotsCache(discovery)

    def _site_host(self, url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

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

    async def _http_with_retry(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> tuple[Any | None, HttpFetchError | None]:
        """M-16 有界重试走 RetryDecision（分类 + Retry-After + jitter）+ Domain Breaker 门禁。

        D-013：先分类错误再选恢复策略；RETRYABLE 类才重试，auth/quota/404 等不重试不升级。
        非 retryable 异常保留原始 FetchErrorCode（SIZE_LIMIT/SSRF 等），不丢失分类。
        """
        from app.config import get_settings
        from app.reliability.breaker import CircuitBreakerRepository, CircuitBreakerService
        from app.reliability.capacity import capacity_from_settings
        from app.reliability.errors import (
            classify_fetch_error_code,
            classify_http_error,
            is_domain_breaker_error,
        )
        from app.reliability.retry import decide_retry

        breaker = CircuitBreakerService(
            CircuitBreakerRepository(self._db), capacity_from_settings(get_settings())
        )
        attempts = 0
        max_attempts = self._max_internal_retries + 1
        while True:
            allowed, _ = breaker.allow_request(url)
            if not allowed:
                # 域名熔断 OPEN：停止无意义请求（不把 404/凭据类计入，见 is_domain_breaker_error）
                return None, HttpFetchError(FetchErrorCode.SERVER_ERROR, "domain circuit open")
            started = time.monotonic()
            body: Any | None = None
            original: HttpFetchError | None = None
            try:
                body = await self._http.get_bytes(url, headers=headers)
            except HttpFetchError as exc:
                original = exc
                error_class = classify_fetch_error_code(exc.code)
            except Exception as exc:  # transport 异常 → canonical 分类
                mapped = map_transport_error(exc)
                original = mapped
                error_class = classify_fetch_error_code(mapped.code)
            if body is None:
                if is_domain_breaker_error(error_class):
                    breaker.record_failure(url, error_class, "fetch failed")
                d = decide_retry(
                    error_class=error_class, attempt=attempts, max_attempts=max_attempts
                )
                if not d.should_retry:
                    # 保留原始错误码（SIZE_LIMIT/SSRF/STORAGE 等），不丢失分类
                    return None, original
                await asyncio.sleep(d.delay_seconds)
                attempts += 1
                continue
            if body.status_code == 429 or 500 <= body.status_code < 600:
                ec = classify_http_error(body.status_code)
                if is_domain_breaker_error(ec):
                    breaker.record_failure(url, ec, f"http {body.status_code}")
                d = decide_retry(
                    error_class=ec,
                    attempt=attempts,
                    max_attempts=max_attempts,
                    retry_after_seconds=_retry_after_from_body(body),
                )
                if not d.should_retry:
                    code = (
                        FetchErrorCode.RATE_LIMITED
                        if body.status_code == 429
                        else FetchErrorCode.SERVER_ERROR
                    )
                    return None, HttpFetchError(code, f"http {body.status_code}")
                await asyncio.sleep(d.delay_seconds)
                attempts += 1
                continue
            if 200 <= body.status_code < 300:
                breaker.record_success(url)  # 仅 2xx 记为成功；401/404 不计数（不计入 domain 崩溃）
            body.duration_ms = int((time.monotonic() - started) * 1000)
            return body, None

    async def process_row(
        self, run: Run, spec: Any, row: URLResource, robots: RobotsCache
    ) -> FetchResult:
        """单 URL 静态层抓取（FetchNodeExecutor 与 ScrapyBatchFetcher 共享同一路径）。"""
        policy = await robots.get(row.url)
        decision = decide_access(
            row.url, spec=spec.payload, robots_policy=policy, user_agent=self._user_agent
        )
        if decision != AccessDecision.ALLOW:
            reason = f"fetch_access_{decision.value}"
            UrlFrontierRepository(self._db).mark_fetch_outcome(
                user_id=run.user_id,
                task_id=row.task_id,
                url_hash=row.url_hash,
                state=FrontierState.BLOCKED,
                error_code=reason,
            )
            return FetchResult(
                status="FAILED",
                tool="http",
                tool_version="1.0",
                error_code=FetchErrorCode.ACCESS_DENIED,
                error_summary=reason,
            )

        host = self._site_host(row.url)
        # 站点策略优先（D-009 策略复用）：已验证 browser 策略 → 直接 BROWSER_PENDING
        if self._site_strategy is not None:
            strategy = self._site_strategy.decide(user_id=run.user_id, site_host=host)
            if strategy is not None and getattr(strategy, "preferred_tier", "") == "browser":
                UrlFrontierRepository(self._db).mark_fetch_outcome(
                    user_id=run.user_id,
                    task_id=row.task_id,
                    url_hash=row.url_hash,
                    state=FrontierState.BROWSER_PENDING,
                    error_code=None,
                )
                return FetchResult(
                    status="BROWSER_PENDING",
                    tool="http",
                    tool_version="1.0",
                    escalation_decision=True,
                    escalation_evidence=EscalationEvidence(
                        kind=EscalationKind.SITE_STRATEGY_BROWSER,
                        detail=f"site strategy prefers browser for {host}",
                        trigger_tool="http",
                    ),
                )

        # 凭据附着（如任务级凭据已批准可用）
        credential_ref = None
        headers = None
        if self._credential_resolver is not None:
            cred = self._credential_resolver.resolve_ref(
                user_id=run.user_id, task_id=run.task_id, domain=host
            )
            if cred:
                credential_ref = cred
                headers = self._credential_resolver.build_headers(
                    user_id=run.user_id, credential_ref=cred, url=row.url
                )

        body, err = await self._http_with_retry(row.url, headers=headers)
        if err is not None:
            return self._handle_fetch_error(
                run, row, err, credential_used=credential_ref is not None
            )

        assert body is not None
        # 状态码驱动的 auth/access/not-found 处理（401/403 不是 Playwright 升级理由）
        if body.status_code == 401:
            return self._handle_auth_response(
                run, row, body, credential_used=credential_ref is not None, host=host
            )
        if body.status_code == 403:
            return self._handle_denied_response(run, row, body)
        if body.status_code == 404:
            return self._handle_not_found(run, row, body)
        if not (200 <= body.status_code < 300):
            return self._handle_unsupported(run, row, body)

        # captcha：绝不自动绕过 / 第三方打码 / 无限重试（三十二）
        if contains_captcha(body.body):
            UrlFrontierRepository(self._db).mark_fetch_outcome(
                user_id=run.user_id,
                task_id=row.task_id,
                url_hash=row.url_hash,
                state=FrontierState.FETCH_FAILED,
                error_code=FetchErrorCode.CAPTCHA_REQUIRED.value,
            )
            return FetchResult(
                status="FAILED",
                tool="http",
                tool_version="1.0",
                http_status=body.status_code,
                content_type=body.content_type,
                download_bytes=len(body.body),
                duration_ms=body.duration_ms,
                error_code=FetchErrorCode.CAPTCHA_REQUIRED,
                error_summary="检测到验证码/挑战，不能自动绕过",
            )

        content_class = classify_content(
            url=row.url, content_type=body.content_type, body=body.body
        )
        evidence = build_escalation_evidence(content_class)
        snapshot = self._snapshot_service(run)

        if evidence is None:
            # 静态/结构化成功 → PageSnapshot → FETCHED（静态页面不启动 Playwright）
            ref = await snapshot.commit_raw(
                body=body.body,
                url_resource_id=row.id,
                tool="http",
                tool_version="1.0",
                source_url=row.url,
                final_url=body.final_url,
                http_status=body.status_code,
                content_type=body.content_type,
                content_length=len(body.body),
                duration_ms=body.duration_ms,
                redirect_summary=body.redirect_chain,
                credential_ref=credential_ref,
                http_metadata=body.headers_allowlist,
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
                    tier="static",
                    tool="http",
                    tool_version="1.0",
                    structure_fingerprint=None,
                    credential_required=credential_ref is not None,
                    credential_type=(credential_ref or {}).get("type") if credential_ref else None,
                )
            return FetchResult(
                status="SUCCESS",
                tool="http",
                tool_version="1.0",
                http_status=body.status_code,
                content_type=body.content_type,
                content_length=len(body.body),
                download_bytes=len(body.body),
                duration_ms=body.duration_ms,
                redirect_summary=body.redirect_chain,
                snapshot_ref=ref,
                retryable=False,
            )

        # 空/JS shell：保留 HTTP attempt 证据 + 升级标记（不删除 HTTP attempt，保留升级链）
        shell_ref = await snapshot.commit_raw(
            body=body.body,
            url_resource_id=row.id,
            tool="http",
            tool_version="1.0",
            source_url=row.url,
            final_url=body.final_url,
            http_status=body.status_code,
            content_type=body.content_type,
            content_length=len(body.body),
            duration_ms=body.duration_ms,
            redirect_summary=body.redirect_chain,
            escalation_evidence=evidence.model_dump(mode="json"),
            credential_ref=credential_ref,
            http_metadata=body.headers_allowlist,
        )
        UrlFrontierRepository(self._db).mark_fetch_outcome(
            user_id=run.user_id,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.BROWSER_PENDING,
            error_code=None,
        )
        return FetchResult(
            status="BROWSER_PENDING",
            tool="http",
            tool_version="1.0",
            http_status=body.status_code,
            content_type=body.content_type,
            download_bytes=len(body.body),
            duration_ms=body.duration_ms,
            snapshot_ref=shell_ref,
            escalation_decision=True,
            escalation_evidence=evidence,
            retryable=False,
        )

    def _handle_auth_response(
        self, run, row, body, *, credential_used: bool, host: str
    ) -> FetchResult:
        """401：无凭据 → CREDENTIAL_REQUIRED；凭据仍失败 → ACCESS_DENIED。"""
        frontier = UrlFrontierRepository(self._db)
        if credential_used:
            frontier.mark_fetch_outcome(
                user_id=run.user_id,
                task_id=row.task_id,
                url_hash=row.url_hash,
                state=FrontierState.FETCH_FAILED,
                error_code=FetchErrorCode.ACCESS_DENIED.value,
            )
            return FetchResult(
                status="FAILED",
                tool="http",
                tool_version="1.0",
                http_status=body.status_code,
                error_code=FetchErrorCode.ACCESS_DENIED,
                error_summary="凭据访问被拒绝",
            )
        frontier.mark_fetch_outcome(
            user_id=run.user_id,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.WAITING_CREDENTIAL,
            error_code=FetchErrorCode.CREDENTIAL_REQUIRED.value,
        )
        return FetchResult(
            status="CREDENTIAL_REQUIRED",
            tool="http",
            tool_version="1.0",
            http_status=body.status_code,
            error_code=FetchErrorCode.AUTH_REQUIRED,
            error_summary="需要网站凭据",
        )

    def _handle_denied_response(self, run, row, body) -> FetchResult:
        """403：访问被拒，不是凭据可修复/可升级；不盲升级 Playwright。"""
        UrlFrontierRepository(self._db).mark_fetch_outcome(
            user_id=run.user_id,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.FETCH_FAILED,
            error_code=FetchErrorCode.ACCESS_DENIED.value,
        )
        return FetchResult(
            status="FAILED",
            tool="http",
            tool_version="1.0",
            http_status=body.status_code,
            error_code=FetchErrorCode.ACCESS_DENIED,
            error_summary="访问被拒绝（403）",
        )

    def _handle_not_found(self, run, row, body) -> FetchResult:
        UrlFrontierRepository(self._db).mark_fetch_outcome(
            user_id=run.user_id,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.FETCH_FAILED,
            error_code=FetchErrorCode.NOT_FOUND.value,
        )
        return FetchResult(
            status="FAILED",
            tool="http",
            tool_version="1.0",
            http_status=body.status_code,
            error_code=FetchErrorCode.NOT_FOUND,
            error_summary="页面不存在（404）",
        )

    def _handle_unsupported(self, run, row, body) -> FetchResult:
        UrlFrontierRepository(self._db).mark_fetch_outcome(
            user_id=run.user_id,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.FETCH_FAILED,
            error_code=FetchErrorCode.UNSUPPORTED_RESPONSE.value,
        )
        return FetchResult(
            status="FAILED",
            tool="http",
            tool_version="1.0",
            http_status=body.status_code,
            error_code=FetchErrorCode.UNSUPPORTED_RESPONSE,
            error_summary=f"不支持的响应状态 {body.status_code}",
        )

    def _handle_fetch_error(
        self, run: Run, row: URLResource, err: HttpFetchError, *, credential_used: bool
    ) -> FetchResult:
        frontier = UrlFrontierRepository(self._db)
        if err.code in (FetchErrorCode.AUTH_REQUIRED, FetchErrorCode.ACCESS_DENIED):
            # 防御分支：异常式 401/403（正常由状态码处理）；有凭据仍失败 → ACCESS_DENIED
            if credential_used:
                frontier.mark_fetch_outcome(
                    user_id=run.user_id,
                    task_id=row.task_id,
                    url_hash=row.url_hash,
                    state=FrontierState.FETCH_FAILED,
                    error_code=FetchErrorCode.ACCESS_DENIED.value,
                )
                return FetchResult(
                    status="FAILED",
                    tool="http",
                    tool_version="1.0",
                    error_code=FetchErrorCode.ACCESS_DENIED,
                    error_summary="凭据访问被拒绝",
                )
            frontier.mark_fetch_outcome(
                user_id=run.user_id,
                task_id=row.task_id,
                url_hash=row.url_hash,
                state=FrontierState.WAITING_CREDENTIAL,
                error_code=FetchErrorCode.CREDENTIAL_REQUIRED.value,
            )
            return FetchResult(
                status="CREDENTIAL_REQUIRED",
                tool="http",
                tool_version="1.0",
                error_code=FetchErrorCode.AUTH_REQUIRED,
                error_summary="需要网站凭据",
            )
        frontier.mark_fetch_outcome(
            user_id=run.user_id,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.FETCH_FAILED,
            error_code=err.code.value,
        )
        if self._site_strategy is not None:
            host = self._site_host(row.url)
            self._site_strategy.record_failure(user_id=run.user_id, site_host=host)
        return FetchResult(
            status="FAILED",
            tool="http",
            tool_version="1.0",
            error_code=err.code,
            error_summary=err.code.value,
            retryable=err.code in (FetchErrorCode.SERVER_ERROR, FetchErrorCode.RATE_LIMITED),
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
        ready = frontier.list_ready_for_fetch(
            user_id=run.user_id, task_id=run.task_id, limit=self._max_batch
        )
        if not ready:
            return ExecuteUnitResult(
                unit_index=unit.index, status="OK", committed_refs={"fetched": 0, "run_id": run.id}
            )

        robots = self.robots_cache()
        results: list[FetchResult] = []
        credential_required_url: URLResource | None = None
        for row in ready:
            result = await self.process_row(run, spec, row, robots)
            results.append(result)
            if result.status == "CREDENTIAL_REQUIRED" and credential_required_url is None:
                credential_required_url = row
            self._emit_event(run, result, row)
        self._db.commit()

        if credential_required_url is not None:
            host = self._site_host(credential_required_url.url)
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="CREDENTIAL_REQUIRED",
                committed_refs={
                    "url_hash": credential_required_url.url_hash,
                    "domain": host,
                    "task_id": run.task_id,
                    "parameters": {
                        "url": credential_required_url.url,
                        "domain": host,
                        "task_id": run.task_id,
                    },
                    "run_id": run.id,
                    "node_id": unit.node_id,
                    "node_type": unit.node_type,
                },
            )

        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "fetched": sum(1 for r in results if r.status == "SUCCESS"),
                "browser_pending": sum(1 for r in results if r.status == "BROWSER_PENDING"),
                "failed": sum(1 for r in results if r.status == "FAILED"),
                "snapshots": [
                    r.snapshot_ref.model_dump(mode="json")
                    for r in results
                    if r.snapshot_ref is not None
                ],
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
            },
        )

    def _emit_event(self, run: Run, result: FetchResult, row: URLResource) -> None:
        """聚合重要事件（D-039）：credential_required / escalated / completed / failed。"""
        from app.state.events import append_domain_event

        payload: dict[str, Any] = {
            "url": row.url,
            "url_hash": row.url_hash,
            "tool": result.tool,
            "status": result.status,
        }
        if result.status == "CREDENTIAL_REQUIRED":
            append_domain_event(
                self._db,
                user_id=run.user_id,
                aggregate_type="task",
                aggregate_id=run.task_id,
                event_type="fetch.credential_required",
                aggregate_version=1,
                payload=payload,
                actor_type="system",
                run_id=run.id,
                node_run_id=None,
            )
            # D-059：Chat 出现“需要凭据”卡片（ref_type=credential_required，meta 带 domain）
            from app.domain.models import ChatMessage

            self._db.add(
                ChatMessage(
                    user_id=run.user_id,
                    task_id=run.task_id,
                    role="assistant",
                    content="该页面需要网站凭据才能访问，请提供凭据。",
                    ref_type="credential_required",
                    ref_id=row.id,
                    meta={
                        "url": row.url,
                        "domain": self._site_host(row.url),
                        "task_id": run.task_id,
                    },
                )
            )
        elif result.status == "BROWSER_PENDING" and result.escalation_evidence is not None:
            payload["evidence"] = result.escalation_evidence.model_dump(mode="json")
            append_domain_event(
                self._db,
                user_id=run.user_id,
                aggregate_type="task",
                aggregate_id=run.task_id,
                event_type="fetch.escalated",
                aggregate_version=1,
                payload=payload,
                actor_type="system",
                run_id=run.id,
                node_run_id=None,
            )
        elif result.status == "SUCCESS":
            append_domain_event(
                self._db,
                user_id=run.user_id,
                aggregate_type="task",
                aggregate_id=run.task_id,
                event_type="fetch.completed",
                aggregate_version=1,
                payload=payload,
                actor_type="system",
                run_id=run.id,
                node_run_id=None,
            )
        elif result.status == "FAILED":
            payload["error_code"] = result.error_code.value if result.error_code else None
            append_domain_event(
                self._db,
                user_id=run.user_id,
                aggregate_type="task",
                aggregate_id=run.task_id,
                event_type="fetch.failed",
                aggregate_version=1,
                payload=payload,
                actor_type="system",
                run_id=run.id,
                node_run_id=None,
            )
