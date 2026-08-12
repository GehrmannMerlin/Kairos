"""M-15 DeletionService：permanent delete（D-065/D-072）。

先算 manifest → 显式删除 task 拥有的 DB 行（不依赖 FK cascade）→ 对每个 object ref
做跨表跨用户引用复查 → 最后一个引用才物理删除对象。幂等、可恢复：重复执行安全 no-op。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.domain.errors import DomainError
from app.domain.models import (
    Approval,
    Artifact,
    ChatMessage,
    Checkpoint,
    CollectionSpecDraft,
    CollectionSpecVersion,
    CompletionDecision,
    DedupeCluster,
    DomainEvent,
    FieldConflict,
    FieldEvidence,
    NodeAttempt,
    NodeRun,
    OutboxEvent,
    PageSnapshot,
    PlanVersion,
    QualitySnapshot,
    Record,
    RecordFieldOverride,
    RecordReviewAction,
    Run,
    Task,
    URLResource,
    ValidationResult,
)
from app.domain.repository import TaskRepository


@dataclass
class DeletionManifest:
    task_id: int
    deleted_rows: int = 0
    objects_removed: list[str] = field(default_factory=list)
    objects_kept: list[str] = field(default_factory=list)


class DeletionService:
    def __init__(self, db, storage) -> None:
        self._db = db
        self._storage = storage

    def _task_object_refs(self, *, user_id: int, task_id: int) -> list[str]:
        refs: list[str] = list(
            self._db.scalars(
                select(PageSnapshot.storage_ref).where(
                    PageSnapshot.user_id == user_id,
                    PageSnapshot.task_id == task_id,
                    PageSnapshot.storage_ref.is_not(None),
                )
            )
        )
        refs += list(
            self._db.scalars(
                select(Artifact.storage_ref).where(
                    Artifact.user_id == user_id,
                    Artifact.task_id == task_id,
                    Artifact.storage_ref.is_not(None),
                )
            )
        )
        # Checkpoint committed_object_refs（best-effort）
        cps = self._db.scalars(
            select(Checkpoint).where(
                Checkpoint.user_id == user_id, Checkpoint.task_id == task_id
            )
        ).all()
        for cp in cps:
            for v in (cp.committed_object_refs or {}).values():
                if isinstance(v, str) and v.startswith(("snapshots/", "artifacts/")):
                    refs.append(v)
        return list(dict.fromkeys(v for v in refs if v))  # 去重保序

    def _ref_used_elsewhere(self, ref: str) -> bool:
        """跨表跨用户引用复查：DB 事实决定对象是否可物理删除（D-072）。"""
        from sqlalchemy import func

        for model, col in (
            (PageSnapshot, PageSnapshot.storage_ref),
            (Artifact, Artifact.storage_ref),
        ):
            n = self._db.scalar(select(func.count()).select_from(model).where(col == ref))
            if n:
                return True
        for cp in self._db.scalars(select(Checkpoint)):
            if any(
                isinstance(v, str) and v == ref for v in (cp.committed_object_refs or {}).values()
            ):
                return True
        return False

    async def permanent_delete(
        self, *, user_id: int, task_id: int, confirmed: bool
    ) -> DeletionManifest:
        task = TaskRepository(self._db).get_owned(user_id, task_id)
        if not confirmed:
            raise DomainError("永久删除必须二次强确认")
        if task.state != "DELETED":
            raise DomainError("只有已删除任务可以永久删除")

        refs = self._task_object_refs(user_id=user_id, task_id=task_id)
        record_ids = list(
            self._db.scalars(
                select(Record.id).where(Record.user_id == user_id, Record.task_id == task_id)
            )
        )
        node_run_ids = list(
            self._db.scalars(
                select(NodeRun.id).where(NodeRun.user_id == user_id, NodeRun.task_id == task_id)
            )
        )

        # 1) record 级子表：按 record_id 集合删除（FieldEvidence.task_id 可能为 NULL）
        for record_model in (
            RecordFieldOverride,
            RecordReviewAction,
            FieldEvidence,
            FieldConflict,
            ValidationResult,
        ):
            if record_ids:
                self._db.execute(
                    sa_delete(record_model).where(
                        record_model.user_id == user_id, record_model.record_id.in_(record_ids)
                    )
                )
        # 2) task 级子表：按 (user_id, task_id)
        for task_model in (
            DedupeCluster,
            Record,
            Checkpoint,
            Approval,
            ChatMessage,
            CollectionSpecDraft,
            CollectionSpecVersion,
            PlanVersion,
            URLResource,
            PageSnapshot,
            QualitySnapshot,
            CompletionDecision,
            Artifact,
            Run,
        ):
            self._db.execute(
                sa_delete(task_model).where(
                    task_model.user_id == user_id, task_model.task_id == task_id
                )
            )
        # 3) node 级子表：NodeAttempt 无 task_id → 按 node_run_id
        if node_run_ids:
            self._db.execute(
                sa_delete(NodeAttempt).where(
                    NodeAttempt.user_id == user_id, NodeAttempt.node_run_id.in_(node_run_ids)
                )
            )
        self._db.execute(
            sa_delete(NodeRun).where(NodeRun.user_id == user_id, NodeRun.task_id == task_id)
        )
        # 4) task 级事件 + 任务本身
        self._db.execute(
            sa_delete(DomainEvent).where(
                DomainEvent.user_id == user_id,
                DomainEvent.aggregate_type == "task",
                DomainEvent.aggregate_id == task_id,
            )
        )
        self._db.execute(
            sa_delete(OutboxEvent).where(
                OutboxEvent.user_id == user_id,
                OutboxEvent.aggregate_type == "task",
                OutboxEvent.aggregate_id == task_id,
            )
        )
        result = self._db.execute(
            sa_delete(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        deleted = result.rowcount or 1
        self._db.commit()
        # 5) 引用复查后删除无人引用的对象（最后一个引用消失才物理删除）
        removed: list[str] = []
        kept: list[str] = []
        for ref in refs:
            if self._ref_used_elsewhere(ref):
                kept.append(ref)
            else:
                await self._storage.delete(ref)
                removed.append(ref)
        return DeletionManifest(
            task_id=task_id, deleted_rows=deleted, objects_removed=removed, objects_kept=kept
        )
