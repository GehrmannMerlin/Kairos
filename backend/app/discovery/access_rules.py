"""AccessRulesCheck executor（M-09 / D-070 / D-017）。

判定 scheme/host/scope/robots。robots denied 且公共 → JIT Approval（复用 M-08
ApprovalService），task 进入 WAITING_APPROVAL，用户批准后 fingerprint 复验再继续。
以下情况不可通过 robots override 放行 → BLOCKED：
HTTP auth(401/403)、登录墙、验证码、access-controlled、私有/凭据资源。
"""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit

from app.discovery.http import DiscoveryHttp
from app.discovery.robots import DEFAULT_USER_AGENT, RobotsCache, RobotsPolicy

ALLOWED_SCHEMES = {"http", "https"}


class AccessDecision(StrEnum):
    ALLOW = "ALLOW"
    ROBOTS_DENIED_PUBLIC = "ROBOTS_DENIED_PUBLIC"
    AUTH_PRIVATE = "AUTH_PRIVATE"
    CAPTCHA = "CAPTCHA"
    ACCESS_CONTROLLED = "ACCESS_CONTROLLED"
    SCOPE_OUT = "SCOPE_OUT"
    SCHEME_INVALID = "SCHEME_INVALID"


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def decide_access(
    url: str,
    *,
    spec: dict,
    robots_policy: RobotsPolicy,
    user_agent: str = DEFAULT_USER_AGENT,
) -> AccessDecision:
    """URL 级确定性决策（不依赖网络）。HTTP 探测升级 auth/private 在 executor 内完成。"""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        return AccessDecision.SCHEME_INVALID
    seed_hosts = {_host_of(u) for u in (spec.get("source_scope", {}).get("seed_urls") or [])}
    if seed_hosts and _host_of(url) not in seed_hosts:
        return AccessDecision.SCOPE_OUT
    if not robots_policy.allowed(url, user_agent=user_agent):
        return AccessDecision.ROBOTS_DENIED_PUBLIC
    return AccessDecision.ALLOW


class AccessRulesService:
    """AccessRulesCheck executor：决策 + robots override 审批 + 不可覆盖分类。"""

    def __init__(self, db, *, robots=None, http=None, approval=None, user_agent=DEFAULT_USER_AGENT):
        self._db = db
        self._http = http or DiscoveryHttp()
        self._robots = robots or RobotsCache(self._http)
        self._approval = approval
        self._user_agent = user_agent

    def _approval_service(self):
        if self._approval is not None:
            return self._approval
        from app.approval.service import ApprovalService

        return ApprovalService(self._db)

    async def _probe_private(self, url: str) -> AccessDecision:
        """轻量 HEAD 探测：401/403 → auth/private（不可覆盖）。"""
        try:
            probe = await self._http.head(url, timeout_seconds=8.0)
        except Exception:
            return AccessDecision.ALLOW  # 探测失败不升级为不可覆盖（保守允许，由 M-10 处理）
        if probe.status_code in (401, 403):
            return AccessDecision.AUTH_PRIVATE
        return AccessDecision.ALLOW

    async def execute(self, unit):
        from app.activities.execution_seam import ExecuteUnitResult
        from app.approval.schemas import ApprovalScope
        from app.discovery.frontier import UrlFrontierRepository
        from app.discovery.models import FrontierState
        from app.domain.models import Run
        from app.domain.repository import SpecVersionRepository, TaskRepository
        from app.domain.service import DomainService

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
        params = unit.parameters or {}
        respect_robots = bool(params.get("respect_robots", True))
        public_only = bool(params.get("public_only", True))
        frontier = UrlFrontierRepository(self._db)
        pending = frontier.list_by_state(
            user_id=run.user_id, task_id=run.task_id, state=FrontierState.DISCOVERED
        )
        if not pending:
            return ExecuteUnitResult(
                unit_index=unit.index, status="OK", committed_refs={"checked": 0, "run_id": run.id}
            )
        blocked: list[str] = []
        for row in pending[:200]:
            policy = await self._robots.get(row.url) if respect_robots else RobotsPolicy()
            decision = decide_access(
                row.url, spec=spec.payload, robots_policy=policy, user_agent=self._user_agent
            )
            if decision == AccessDecision.ROBOTS_DENIED_PUBLIC and public_only:
                probe = await self._probe_private(row.url)
                if probe == AccessDecision.AUTH_PRIVATE:
                    frontier.mark_blocked(
                        user_id=run.user_id,
                        url_hash=row.url_hash,
                        reason="access_AUTH_PRIVATE_non_overrideable",
                    )
                    blocked.append(row.url_hash)
                    continue
                # 公共且 robots denied → 可 override → JIT Approval
                approval = self._approval_service().request_approval(
                    user_id=run.user_id,
                    task_id=run.task_id,
                    spec_version=run.spec_version,
                    plan_version=run.plan_version,
                    node_id=unit.node_id,
                    node_type=unit.node_type,
                    action_type="robots_override",
                    target=row.url,
                    parameters={"url": row.url, "host": _host_of(row.url)},
                    scope=ApprovalScope.THIS_ACTION,
                )
                task = TaskRepository(self._db).get_owned(run.user_id, run.task_id)
                from contextlib import suppress

                from app.domain.errors import IllegalTransitionError, StaleVersionError

                with suppress(IllegalTransitionError, StaleVersionError):
                    # 幂等：已在 WAITING_APPROVAL 视为成功
                    DomainService(TaskRepository(self._db)).transition_task(
                        user_id=run.user_id,
                        task_id=run.task_id,
                        command="mark_waiting_approval",
                        expected_version=task.version,
                        actor_type="system",
                        reason="robots_override_approval",
                    )
                frontier.mark_state(
                    user_id=run.user_id, url_hash=row.url_hash, state=FrontierState.WAITING_APPROVAL
                )
                self._db.commit()
                return ExecuteUnitResult(
                    unit_index=unit.index,
                    status="WAITING_APPROVAL",
                    committed_refs={
                        "approval_id": approval.id,
                        "url_hash": row.url_hash,
                        "parameters": {"url": row.url, "host": _host_of(row.url)},
                        "run_id": run.id,
                        "node_id": unit.node_id,
                        "node_type": unit.node_type,
                    },
                )
            if decision == AccessDecision.ALLOW:
                frontier.mark_state(
                    user_id=run.user_id, url_hash=row.url_hash, state=FrontierState.ACCESS_ALLOWED
                )
            else:
                frontier.mark_blocked(
                    user_id=run.user_id, url_hash=row.url_hash, reason=f"access_{decision.value}"
                )
                blocked.append(row.url_hash)
        self._db.commit()
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "checked": len(pending[:200]),
                "blocked": len(blocked),
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
            },
        )
