"""Owner-scoped repositories for core domain objects (M-04).

Every read takes an explicit ``user_id`` boundary; cross-user access raises the
M-02 ``NotFoundError`` (404) so existence is never revealed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.auth.errors import NotFoundError
from app.domain.models import (
    Checkpoint,
    CollectionSpecVersion,
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

    def create(self, *, user_id: int, title: str, task_type: str = "directed") -> Task:
        task = Task(user_id=user_id, title=title, task_type=task_type, state="draft", version=1)
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


class PlanVersionRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self, *, user_id: int, task_id: int, spec_version: int, version: int, payload: dict
    ) -> PlanVersion:
        row = PlanVersion(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            version=version,
            payload=payload,
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
            state="pending",
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

    def mark_dispatched(self, outbox: OutboxEvent) -> None:
        from datetime import UTC, datetime

        outbox.status = "dispatched"
        outbox.dispatched_at = datetime.now(UTC)
        self._db.commit()

    def mark_failed(self, outbox: OutboxEvent) -> None:
        outbox.status = "failed"
        outbox.attempts += 1
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
    "SpecVersionRepository",
    "PlanVersionRepository",
    "RunRepository",
    "NodeRunRepository",
    "NodeAttemptRepository",
    "RecordRepository",
    "OutboxRepository",
    "IdempotencyRepository",
    "CheckpointRepository",
    "DomainEvent",
]
