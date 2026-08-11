"""Owner-scoped repositories for core domain objects (M-04).

Every read takes an explicit ``user_id`` boundary; cross-user access raises the
M-02 ``NotFoundError`` (404) so existence is never revealed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.auth.errors import NotFoundError
from app.domain.models import (
    Approval,
    ChatMessage,
    Checkpoint,
    CollectionSpecDraft,
    CollectionSpecVersion,
    CollectionTemplate,
    DomainEvent,
    IdempotencyKey,
    NodeAttempt,
    NodeRun,
    OutboxEvent,
    PlanVersion,
    Record,
    Run,
    Task,
)


def _owned(db: Any, model: type, user_id: int, obj_id: int) -> Any:
    row = db.get(model, obj_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("资源不存在")
    return row


class TaskRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        title: str,
        task_type: str | None = None,
        template_id: str | None = None,
        template_version: int | None = None,
    ) -> Task:
        task = Task(
            user_id=user_id,
            title=title,
            task_type=task_type,
            template_id=template_id,
            template_version=template_version,
            state="DRAFT",
            version=1,
        )
        self._db.add(task)
        self._db.commit()
        self._db.refresh(task)
        return task

    def get_owned(self, user_id: int, task_id: int) -> Task:
        return _owned(self._db, Task, user_id, task_id)

    def list_by_user(self, user_id: int) -> list[Task]:
        return list(
            self._db.scalars(
                select(Task)
                .where(Task.user_id == user_id, Task.deleted_at.is_(None))
                .order_by(Task.created_at.desc())
            )
        )

    def update_state(self, task: Task, new_state: str, expected_version: int) -> Task:
        if task.version != expected_version:
            from app.domain.errors import StaleVersionError

            raise StaleVersionError("任务已被其他操作修改")
        task.state = new_state
        task.version = task.version + 1
        self._db.add(task)
        return task


class SpecVersionRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        task_id: int,
        version: int,
        spec_type: str,
        schema_version: str,
        payload: dict,
    ) -> CollectionSpecVersion:
        row = CollectionSpecVersion(
            user_id=user_id,
            task_id=task_id,
            version=version,
            spec_type=spec_type,
            schema_version=schema_version,
            payload=payload,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, spec_id: int) -> CollectionSpecVersion:
        return _owned(self._db, CollectionSpecVersion, user_id, spec_id)

    def get_version(self, user_id: int, task_id: int, version: int) -> CollectionSpecVersion:
        row = self._db.scalar(
            select(CollectionSpecVersion).where(
                CollectionSpecVersion.user_id == user_id,
                CollectionSpecVersion.task_id == task_id,
                CollectionSpecVersion.version == version,
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def latest_version(self, user_id: int, task_id: int) -> CollectionSpecVersion | None:
        return self._db.scalar(
            select(CollectionSpecVersion)
            .where(
                CollectionSpecVersion.user_id == user_id,
                CollectionSpecVersion.task_id == task_id,
            )
            .order_by(CollectionSpecVersion.version.desc())
            .limit(1)
        )

    def next_version(self, user_id: int, task_id: int) -> int:
        latest = self.latest_version(user_id, task_id)
        return (latest.version + 1) if latest is not None else 1

    def mark_confirmed(
        self,
        *,
        user_id: int,
        task_id: int,
        version: int,
        confirmed_by: int,
    ) -> CollectionSpecVersion:
        from datetime import UTC, datetime

        row = self.get_version(user_id, task_id, version)
        row.confirmed_at = datetime.now(UTC)
        row.confirmed_by = confirmed_by
        self._db.add(row)
        return row


class ChatMessageRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        task_id: int,
        role: str,
        content: str,
        ref_type: str | None = None,
        ref_id: int | None = None,
        meta: dict | None = None,
    ) -> ChatMessage:
        row = ChatMessage(
            user_id=user_id,
            task_id=task_id,
            role=role,
            content=content,
            ref_type=ref_type,
            ref_id=ref_id,
            meta=meta,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def list_by_task(self, user_id: int, task_id: int) -> list[ChatMessage]:
        return list(
            self._db.scalars(
                select(ChatMessage)
                .where(ChatMessage.user_id == user_id, ChatMessage.task_id == task_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
        )


class SpecDraftRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def get_for_task(self, user_id: int, task_id: int) -> CollectionSpecDraft | None:
        return self._db.scalar(
            select(CollectionSpecDraft).where(
                CollectionSpecDraft.user_id == user_id, CollectionSpecDraft.task_id == task_id
            )
        )

    def upsert(self, *, user_id: int, task_id: int, payload: dict) -> CollectionSpecDraft:
        row = self.get_for_task(user_id, task_id)
        if row is None:
            row = CollectionSpecDraft(user_id=user_id, task_id=task_id, payload=payload)
            self._db.add(row)
        else:
            row.payload = payload
            self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row


class TemplateRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        name: str,
        task_type: str,
        goal_template: str,
        variables: list,
        field_schema: list,
        completion_conditions: list,
        advanced_settings: dict,
        field_expansion: dict,
        default_model_config_ref: dict | None,
    ) -> CollectionTemplate:
        from uuid import uuid4

        row = CollectionTemplate(
            template_id=uuid4().hex,
            user_id=user_id,
            version=1,
            name=name,
            task_type=task_type,
            goal_template=goal_template,
            variables=variables,
            field_schema=field_schema,
            completion_conditions=completion_conditions,
            advanced_settings=advanced_settings,
            field_expansion=field_expansion,
            default_model_config_ref=default_model_config_ref,
            is_current=True,
            is_favorite=False,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def append_version(
        self,
        *,
        template_id: str,
        user_id: int,
        name: str,
        task_type: str,
        goal_template: str,
        variables: list,
        field_schema: list,
        completion_conditions: list,
        advanced_settings: dict,
        field_expansion: dict,
        default_model_config_ref: dict | None,
    ) -> CollectionTemplate:
        self._unset_current(template_id, user_id)
        version = self.next_version(template_id)
        current = self.get_version(user_id, template_id, max(1, version - 1))
        row = CollectionTemplate(
            template_id=template_id,
            user_id=user_id,
            version=version,
            name=name,
            task_type=task_type,
            goal_template=goal_template,
            variables=variables,
            field_schema=field_schema,
            completion_conditions=completion_conditions,
            advanced_settings=advanced_settings,
            field_expansion=field_expansion,
            default_model_config_ref=default_model_config_ref,
            is_current=True,
            is_favorite=current.is_favorite,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def next_version(self, template_id: str) -> int:
        from sqlalchemy import func

        latest = self._db.scalar(
            select(func.max(CollectionTemplate.version)).where(
                CollectionTemplate.template_id == template_id
            )
        )
        return (latest or 0) + 1

    def get_current(self, user_id: int, template_id: str) -> CollectionTemplate:
        row = self._db.scalar(
            select(CollectionTemplate).where(
                CollectionTemplate.template_id == template_id,
                CollectionTemplate.user_id == user_id,
                CollectionTemplate.is_current.is_(True),
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def get_version(self, user_id: int, template_id: str, version: int) -> CollectionTemplate:
        row = self._db.scalar(
            select(CollectionTemplate).where(
                CollectionTemplate.template_id == template_id,
                CollectionTemplate.user_id == user_id,
                CollectionTemplate.version == version,
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def list_current(self, user_id: int) -> list[CollectionTemplate]:
        return list(
            self._db.scalars(
                select(CollectionTemplate)
                .where(
                    CollectionTemplate.user_id == user_id,
                    CollectionTemplate.is_current.is_(True),
                )
                .order_by(
                    CollectionTemplate.is_favorite.desc(), CollectionTemplate.created_at.desc()
                )
            )
        )

    def set_favorite(self, user_id: int, template_id: str, favorite: bool) -> CollectionTemplate:
        current = self.get_current(user_id, template_id)
        current.is_favorite = favorite
        self._db.add(current)
        self._db.commit()
        self._db.refresh(current)
        return current

    def delete(self, user_id: int, template_id: str) -> None:
        current = self.get_current(user_id, template_id)
        current.is_current = False
        self._db.add(current)
        self._db.commit()

    def _unset_current(self, template_id: str, user_id: int) -> None:
        from sqlalchemy import update

        self._db.execute(
            update(CollectionTemplate)
            .where(
                CollectionTemplate.template_id == template_id,
                CollectionTemplate.user_id == user_id,
                CollectionTemplate.is_current.is_(True),
            )
            .values(is_current=False)
        )
        self._db.commit()


class PlanVersionRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        version: int,
        payload: dict,
        parent_plan_version_id: int | None = None,
        validation_status: str = "pending",
        plan_fingerprint: str = "",
        model_config_id: str | None = None,
        model_config_version: int | None = None,
        registry_versions: dict | None = None,
        generation_policy: str = "auto",
        trigger_reason: str | None = None,
        replan_evidence_refs: list | None = None,
        diff_summary: dict | None = None,
    ) -> PlanVersion:
        row = PlanVersion(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            version=version,
            payload=payload,
            parent_plan_version_id=parent_plan_version_id,
            validation_status=validation_status,
            plan_fingerprint=plan_fingerprint,
            model_config_id=model_config_id,
            model_config_version=model_config_version,
            registry_versions=registry_versions or {},
            generation_policy=generation_policy,
            trigger_reason=trigger_reason,
            replan_evidence_refs=replan_evidence_refs or [],
            diff_summary=diff_summary,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, plan_id: int) -> PlanVersion:
        return _owned(self._db, PlanVersion, user_id, plan_id)

    def get_version(self, user_id: int, task_id: int, version: int) -> PlanVersion:
        row = self._db.scalar(
            select(PlanVersion).where(
                PlanVersion.user_id == user_id,
                PlanVersion.task_id == task_id,
                PlanVersion.version == version,
            )
        )
        if row is None:
            raise NotFoundError("资源不存在")
        return row

    def list_for_task(self, user_id: int, task_id: int) -> list[PlanVersion]:
        return list(
            self._db.scalars(
                select(PlanVersion)
                .where(PlanVersion.user_id == user_id, PlanVersion.task_id == task_id)
                .order_by(PlanVersion.version.desc())
            )
        )

    def latest_version(self, user_id: int, task_id: int) -> PlanVersion | None:
        return self._db.scalar(
            select(PlanVersion)
            .where(PlanVersion.user_id == user_id, PlanVersion.task_id == task_id)
            .order_by(PlanVersion.version.desc())
            .limit(1)
        )

    def next_version(self, user_id: int, task_id: int) -> int:
        latest = self.latest_version(user_id, task_id)
        return (latest.version + 1) if latest is not None else 1


class RunRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(self, *, user_id: int, task_id: int, spec_version: int, plan_version: int) -> Run:
        row = Run(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
            state="pending",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, run_id: int) -> Run:
        return _owned(self._db, Run, user_id, run_id)


class NodeRunRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        run_id: int,
        task_id: int,
        node_type: str,
        input_fingerprint: str | None = None,
    ) -> NodeRun:
        row = NodeRun(
            user_id=user_id,
            run_id=run_id,
            task_id=task_id,
            node_type=node_type,
            input_fingerprint=input_fingerprint,
            state="PENDING",
            version=1,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, node_run_id: int) -> NodeRun:
        return _owned(self._db, NodeRun, user_id, node_run_id)

    def update_state(self, node: NodeRun, new_state: str, expected_version: int) -> NodeRun:
        if node.version != expected_version:
            from app.domain.errors import StaleVersionError

            raise StaleVersionError("节点已被其他操作修改")
        node.state = new_state
        node.version = node.version + 1
        self._db.add(node)
        return node


class NodeAttemptRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def next_attempt(self, node_run_id: int) -> int:
        from sqlalchemy import func

        latest = self._db.scalar(
            select(func.max(NodeAttempt.attempt)).where(NodeAttempt.node_run_id == node_run_id)
        )
        return (latest or 0) + 1

    def create(self, *, user_id: int, node_run_id: int, attempt: int) -> NodeAttempt:
        row = NodeAttempt(
            user_id=user_id, node_run_id=node_run_id, attempt=attempt, status="pending"
        )
        self._db.add(row)
        return row


class RecordRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int | None,
        spec_version: int,
        payload: dict,
        partition: str = "passed",
        business_key: str | None = None,
    ) -> Record:
        row = Record(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            spec_version=spec_version,
            payload=payload,
            partition=partition,
            business_key=business_key,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_owned(self, user_id: int, record_id: int) -> Record:
        return _owned(self._db, Record, user_id, record_id)


class ApprovalRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
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
        parameter_fingerprint: str,
        scope: str,
        approved_scope: str,
        reason: str | None,
        credential_ref: dict | None,
        status_payload: dict | None,
        expires_at: Any,
    ) -> Approval:
        row = Approval(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            plan_version=plan_version,
            node_id=node_id,
            node_type=node_type,
            action_type=action_type,
            target=target,
            parameter_fingerprint=parameter_fingerprint,
            scope=scope,
            approved_scope=approved_scope,
            state="PENDING",
            reason=reason,
            credential_ref=credential_ref,
            status_payload=status_payload,
            expires_at=expires_at,
        )
        self._db.add(row)
        self._db.flush()
        return row

    def get_owned(self, user_id: int, approval_id: int) -> Approval:
        return _owned(self._db, Approval, user_id, approval_id)

    def list_for_task(self, user_id: int, task_id: int) -> list[Approval]:
        return list(
            self._db.scalars(
                select(Approval)
                .where(Approval.user_id == user_id, Approval.task_id == task_id)
                .order_by(Approval.created_at.desc())
            )
        )

    def list_pending_for_task(self, user_id: int, task_id: int) -> list[Approval]:
        return list(
            self._db.scalars(
                select(Approval)
                .where(
                    Approval.user_id == user_id,
                    Approval.task_id == task_id,
                    Approval.state == "PENDING",
                )
                .order_by(Approval.created_at.desc())
            )
        )

    def list_pending_by_user(self, user_id: int) -> list[Approval]:
        return list(
            self._db.scalars(
                select(Approval)
                .where(Approval.user_id == user_id, Approval.state == "PENDING")
                .order_by(Approval.created_at.desc())
            )
        )


# 有界重试上限：Signal 分发失败后先保留 retryable（pending，attempts+1），
# 达到上限后才终态 failed。终态 failed 不再被 claim，由后续 API 命令重新触发新事件。
OUTBOX_MAX_ATTEMPTS = 3


class OutboxRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def claim_pending(self, *, limit: int = 50) -> list[OutboxEvent]:
        return list(
            self._db.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.status == "pending")
                .order_by(OutboxEvent.id)
                .limit(limit)
            )
        )

    def claim_pending_for_aggregate(
        self, *, user_id: int, aggregate_type: str, aggregate_id: int
    ) -> list[OutboxEvent]:
        """Pending outbox events for ONE aggregate (owner-scoped, ordered).

        The dispatcher filters by aggregate so one task's command events never
        starve another task's pending rows.
        """
        return list(
            self._db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.user_id == user_id,
                    OutboxEvent.aggregate_type == aggregate_type,
                    OutboxEvent.aggregate_id == aggregate_id,
                    OutboxEvent.status == "pending",
                )
                .order_by(OutboxEvent.id)
            )
        )

    def mark_dispatched(self, outbox: OutboxEvent) -> None:
        from datetime import UTC, datetime

        outbox.status = "dispatched"
        outbox.dispatched_at = datetime.now(UTC)
        self._db.commit()

    def mark_failed(self, outbox: OutboxEvent) -> None:
        # 有界重试：达到上限前保持 pending（可被后续 dispatch_pending_for 重新 claim
        # 并补发），避免一次 Signal 失败把行永久孤儿化。
        outbox.attempts += 1
        if outbox.attempts >= OUTBOX_MAX_ATTEMPTS:
            outbox.status = "failed"
        else:
            outbox.status = "pending"
        self._db.commit()


class IdempotencyRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def find(self, *, user_id: int, operation: str, key: str) -> IdempotencyKey | None:
        return self._db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.operation == operation,
                IdempotencyKey.idempotency_key == key,
            )
        )

    def create(
        self,
        *,
        user_id: int,
        operation: str,
        key: str,
        payload_fingerprint: str,
        result_ref_type: str,
        result_ref_id: int,
    ) -> IdempotencyKey:
        row = IdempotencyKey(
            user_id=user_id,
            operation=operation,
            idempotency_key=key,
            payload_fingerprint=payload_fingerprint,
            result_ref_type=result_ref_type,
            result_ref_id=result_ref_id,
        )
        self._db.add(row)
        return row


class CheckpointRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def find_by_batch(self, run_id: int, batch_identity: str) -> Checkpoint | None:
        return self._db.scalar(
            select(Checkpoint).where(
                Checkpoint.run_id == run_id, Checkpoint.batch_identity == batch_identity
            )
        )

    def create(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        batch_identity: str,
        spec_version: int,
        plan_version: int,
        node_run_id: int | None,
        input_fingerprint: str,
        committed_object_refs: dict,
        content_hash: str | None,
    ) -> Checkpoint:
        row = Checkpoint(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            batch_identity=batch_identity,
            spec_version=spec_version,
            plan_version=plan_version,
            node_run_id=node_run_id,
            input_fingerprint=input_fingerprint,
            committed_object_refs=committed_object_refs,
            content_hash=content_hash,
        )
        self._db.add(row)
        return row


# Re-exported for convenience in service/tests.
__all__ = [
    "TaskRepository",
    "ChatMessageRepository",
    "SpecDraftRepository",
    "TemplateRepository",
    "SpecVersionRepository",
    "PlanVersionRepository",
    "RunRepository",
    "NodeRunRepository",
    "NodeAttemptRepository",
    "RecordRepository",
    "ApprovalRepository",
    "OutboxRepository",
    "IdempotencyRepository",
    "CheckpointRepository",
    "DomainEvent",
]
