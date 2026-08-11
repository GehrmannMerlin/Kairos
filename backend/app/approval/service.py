"""ApprovalService — JIT 审批生命周期（M-08 / D-017）。

Approval 不是永久授权：每次消费前重新校验 owner / spec_version / plan_version /
node identity / parameter_fingerprint / scope / expiry / consumed|revoked 状态。
状态转换走 DomainEvent + Outbox（同一事务），不直接 Signal Temporal。

禁止：GLOBAL_FOREVER / ALL_TASKS / ANY_PARAMETERS；PROHIBITED 动作不能创建 Approval。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.approval.schemas import ApprovalScope, ApprovalState
from app.domain.errors import DomainError
from app.domain.idempotency import stable_fingerprint
from app.domain.models import Approval
from app.domain.repository import ApprovalRepository, TaskRepository
from app.state.events import append_domain_event, enqueue_outbox

DEFAULT_APPROVAL_TTL_MINUTES = 30


def approval_fingerprint(action_type: str, parameters: dict) -> str:
    return stable_fingerprint("approval", action_type, parameters)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite 回读的 datetime 是 naive；统一补成 UTC 以便与 aware _now() 比较。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class ApprovalService:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._repo = ApprovalRepository(db)

    def request_approval(
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        plan_version: int | None,
        node_id: str | None,
        node_type: str | None,
        action_type: str,
        target: str | None,
        parameters: dict,
        scope: ApprovalScope,
        reason: str | None = None,
        credential_ref: dict | str | None = None,
        status_payload: dict | None = None,
        expires_at: datetime | None = None,
    ) -> Approval:
        TaskRepository(self._db).get_owned(user_id, task_id)
        row = self._repo.create(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
            node_id=node_id,
            node_type=node_type,
            action_type=action_type,
            target=target,
            parameter_fingerprint=approval_fingerprint(action_type, parameters),
            scope=scope.value,
            approved_scope=scope.value,
            reason=reason,
            credential_ref=credential_ref,
            status_payload=status_payload,
            expires_at=expires_at or (_now() + timedelta(minutes=DEFAULT_APPROVAL_TTL_MINUTES)),
        )
        payload = {
            "approval_id": row.id,
            "action_type": action_type,
            "node_id": node_id,
            "state": ApprovalState.PENDING.value,
        }
        append_domain_event(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="approval.requested",
            aggregate_version=1,
            payload=payload,
            actor_type="system",
            node_run_id=None,
        )
        enqueue_outbox(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=task_id,
            event_type="approval.requested",
            payload=payload,
            dispatch_key=f"task:{task_id}:approval:{row.id}",
        )
        self._db.commit()
        self._db.refresh(row)
        return row

    def _transition(
        self, *, user_id: int, approval_id: int, target_state: ApprovalState, actor_id: int | None
    ) -> Approval:
        row = self.get_owned(user_id, approval_id)
        if row.state != ApprovalState.PENDING.value:
            raise DomainError("该审批已处理，不能重复操作")
        row.state = target_state.value
        row.resolved_at = _now()
        if actor_id is not None:
            row.resolved_by = actor_id
        self._db.add(row)
        event_type = {
            ApprovalState.APPROVED: "approval.approved",
            ApprovalState.REJECTED: "approval.rejected",
            ApprovalState.REVOKED: "approval.revoked",
        }[target_state]
        payload = {
            "approval_id": approval_id,
            "action_type": row.action_type,
            "state": target_state.value,
            "parameter_fingerprint": row.parameter_fingerprint,
            "spec_version": row.spec_version,
        }
        append_domain_event(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=row.task_id,
            event_type=event_type,
            aggregate_version=2,
            payload=payload,
            actor_type="user",
            actor_id=actor_id or user_id,
        )
        enqueue_outbox(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=row.task_id,
            event_type=event_type,
            payload=payload,
            dispatch_key=f"task:{row.task_id}:approval:{approval_id}:{target_state.value}",
        )
        self._db.commit()
        self._db.refresh(row)
        return row

    def approve(self, *, user_id: int, approval_id: int, actor_id: int | None = None) -> Approval:
        return self._transition(
            user_id=user_id,
            approval_id=approval_id,
            target_state=ApprovalState.APPROVED,
            actor_id=actor_id,
        )

    def reject(self, *, user_id: int, approval_id: int, actor_id: int | None = None) -> Approval:
        return self._transition(
            user_id=user_id,
            approval_id=approval_id,
            target_state=ApprovalState.REJECTED,
            actor_id=actor_id,
        )

    def revoke(self, *, user_id: int, approval_id: int, actor_id: int | None = None) -> Approval:
        return self._transition(
            user_id=user_id,
            approval_id=approval_id,
            target_state=ApprovalState.REVOKED,
            actor_id=actor_id,
        )

    def _check_consumable(self, row: Approval, *, parameters: dict) -> None:
        """消费前复验：owner/spec/plan/fingerprint/expiry/state（D-017 失效规则）。"""
        if row.state not in (ApprovalState.PENDING.value, ApprovalState.APPROVED.value):
            raise DomainError("该审批不能消费")
        expires = _as_utc(row.expires_at)
        if expires is not None and expires < _now():
            raise DomainError("该审批已过期")
        fp = approval_fingerprint(row.action_type, parameters)
        if row.parameter_fingerprint != fp:
            raise DomainError("审批参数已变化，原授权失效")

    def consume(self, *, user_id: int, approval_id: int, parameters: dict) -> Approval:
        row = self.get_owned(user_id, approval_id)
        self._check_consumable(row, parameters=parameters)
        row.state = ApprovalState.CONSUMED.value
        row.consumed_at = _now()
        self._db.add(row)
        payload = {
            "approval_id": approval_id,
            "action_type": row.action_type,
            "state": ApprovalState.CONSUMED.value,
        }
        append_domain_event(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=row.task_id,
            event_type="approval.consumed",
            aggregate_version=3,
            payload=payload,
            actor_type="system",
        )
        enqueue_outbox(
            self._db,
            user_id=user_id,
            aggregate_type="task",
            aggregate_id=row.task_id,
            event_type="approval.consumed",
            payload=payload,
            dispatch_key=f"task:{row.task_id}:approval:{approval_id}:consumed",
        )
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, approval_id: int) -> Approval:
        return self._repo.get_owned(user_id, approval_id)

    def list_for_task(self, user_id: int, task_id: int) -> list[Approval]:
        return self._repo.list_for_task(user_id, task_id)

    def list_pending_for_task(self, user_id: int, task_id: int) -> list[Approval]:
        return self._repo.list_pending_for_task(user_id, task_id)

    def list_pending_by_user(self, user_id: int) -> list[Approval]:
        return self._repo.list_pending_by_user(user_id)
