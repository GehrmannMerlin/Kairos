"""M-14 Execution read-model repository：读取 Run/DomainEvent/URLResource/Record/PlanVersion。

所有查询强制 user_id + task_id 边界。只读，不写任何业务状态。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain.models import (
    DomainEvent,
    NodeAttempt,
    NodeRun,
    PlanVersion,
    Record,
    Run,
    URLResource,
)

_COVERED_STATUSES = ("FETCHED", "HANDED_OFF")
_TERMINAL_FAILED = "FAILED"


class ExecutionRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def latest_run(self, *, user_id: int, task_id: int) -> Run | None:
        return self._db.scalar(
            select(Run)
            .where(Run.user_id == user_id, Run.task_id == task_id)
            .order_by(Run.id.desc())
            .limit(1)
        )

    def latest_plan(self, *, user_id: int, task_id: int) -> PlanVersion | None:
        return self._db.scalar(
            select(PlanVersion)
            .where(PlanVersion.user_id == user_id, PlanVersion.task_id == task_id)
            .order_by(PlanVersion.version.desc())
            .limit(1)
        )

    def plan_version(self, *, user_id: int, task_id: int, version: int) -> PlanVersion | None:
        return self._db.scalar(
            select(PlanVersion).where(
                PlanVersion.user_id == user_id,
                PlanVersion.task_id == task_id,
                PlanVersion.version == version,
            )
        )

    def node_runs(self, *, user_id: int, task_id: int, run_id: int) -> list[NodeRun]:
        return list(
            self._db.scalars(
                select(NodeRun)
                .where(
                    NodeRun.user_id == user_id,
                    NodeRun.task_id == task_id,
                    NodeRun.run_id == run_id,
                )
                .order_by(NodeRun.position, NodeRun.id)
            )
        )

    def latest_attempt(self, *, user_id: int, node_run_id: int) -> NodeAttempt | None:
        return self._db.scalar(
            select(NodeAttempt)
            .where(
                NodeAttempt.user_id == user_id,
                NodeAttempt.node_run_id == node_run_id,
            )
            .order_by(NodeAttempt.attempt.desc(), NodeAttempt.id.desc())
            .limit(1)
        )

    def url_stats(self, *, user_id: int, task_id: int) -> dict[str, int]:
        return self._url_stats(
            URLResource.user_id == user_id,
            URLResource.task_id == task_id,
        )

    def run_url_stats(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
    ) -> dict[str, int]:
        return self._url_stats(
            URLResource.user_id == user_id,
            URLResource.task_id == task_id,
            URLResource.run_id == run_id,
            URLResource.spec_version == spec_version,
        )

    def run_url_hashes(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
    ) -> set[str]:
        return set(
            self._db.scalars(
                select(URLResource.url_hash).where(
                    URLResource.user_id == user_id,
                    URLResource.task_id == task_id,
                    URLResource.run_id == run_id,
                    URLResource.spec_version == spec_version,
                )
            )
        )

    def _url_stats(self, *conditions: Any) -> dict[str, int]:
        rows = self._db.execute(
            select(URLResource.status, func.count()).where(*conditions).group_by(URLResource.status)
        ).all()
        status_counts = {s: int(c) for s, c in rows}
        total = sum(status_counts.values())
        fetched = sum(c for s, c in status_counts.items() if s in _COVERED_STATUSES)
        failed = sum(c for s, c in status_counts.items() if s == _TERMINAL_FAILED)
        return {
            "discovered": total,
            "fetched": fetched,
            "failed": failed,
            "pending": total - fetched - failed,
        }

    def record_counts(self, *, user_id: int, task_id: int) -> dict[str, int]:
        rows = self._db.execute(
            select(Record.partition, func.count())
            .where(Record.user_id == user_id, Record.task_id == task_id)
            .group_by(Record.partition)
        ).all()
        return {p: int(c) for p, c in rows}

    def events_after(
        self,
        *,
        user_id: int,
        task_id: int,
        after_id: int,
        limit: int,
        through_id: int | None = None,
    ) -> list[DomainEvent]:
        """task 作用域事件 + 本 task 的 record.* 事件（同 M-07 query_task_events）。"""
        record_ids = select(Record.id).where(Record.user_id == user_id, Record.task_id == task_id)
        from sqlalchemy import and_, or_

        id_conditions = [DomainEvent.id > after_id]
        if through_id is not None:
            id_conditions.append(DomainEvent.id <= through_id)
        return list(
            self._db.scalars(
                select(DomainEvent)
                .where(
                    DomainEvent.user_id == user_id,
                    *id_conditions,
                    or_(
                        and_(
                            DomainEvent.aggregate_type == "task",
                            DomainEvent.aggregate_id == task_id,
                        ),
                        and_(
                            DomainEvent.aggregate_type == "record",
                            DomainEvent.aggregate_id.in_(record_ids),
                        ),
                    ),
                )
                .order_by(DomainEvent.id)
                .limit(limit)
            )
        )

    def max_event_id(self, *, user_id: int, task_id: int) -> int:
        record_ids = select(Record.id).where(Record.user_id == user_id, Record.task_id == task_id)
        from sqlalchemy import and_, or_

        return int(
            self._db.scalar(
                select(func.max(DomainEvent.id)).where(
                    DomainEvent.user_id == user_id,
                    or_(
                        and_(
                            DomainEvent.aggregate_type == "task",
                            DomainEvent.aggregate_id == task_id,
                        ),
                        and_(
                            DomainEvent.aggregate_type == "record",
                            DomainEvent.aggregate_id.in_(record_ids),
                        ),
                    ),
                )
            )
            or 0
        )

    def has_event_after(self, *, user_id: int, task_id: int, after_id: int) -> bool:
        record_ids = select(Record.id).where(Record.user_id == user_id, Record.task_id == task_id)
        from sqlalchemy import and_, or_

        row = self._db.scalar(
            select(DomainEvent.id)
            .where(
                DomainEvent.user_id == user_id,
                DomainEvent.id > after_id,
                or_(
                    and_(
                        DomainEvent.aggregate_type == "task",
                        DomainEvent.aggregate_id == task_id,
                    ),
                    and_(
                        DomainEvent.aggregate_type == "record",
                        DomainEvent.aggregate_id.in_(record_ids),
                    ),
                ),
            )
            .order_by(DomainEvent.id)
            .limit(1)
        )
        return row is not None

    def record_count_total(self, *, user_id: int, task_id: int) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(Record)
                .where(Record.user_id == user_id, Record.task_id == task_id)
            )
            or 0
        )

    def run_record_count_total(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
    ) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(Record)
                .where(
                    Record.user_id == user_id,
                    Record.task_id == task_id,
                    Record.run_id == run_id,
                    Record.spec_version == spec_version,
                )
            )
            or 0
        )

    def validated_record_count(self, *, user_id: int, task_id: int) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(Record)
                .where(
                    Record.user_id == user_id,
                    Record.task_id == task_id,
                    Record.validated_at.is_not(None),
                )
            )
            or 0
        )

    def run_validated_record_count(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
    ) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(Record)
                .where(
                    Record.user_id == user_id,
                    Record.task_id == task_id,
                    Record.run_id == run_id,
                    Record.spec_version == spec_version,
                    Record.validated_at.is_not(None),
                )
            )
            or 0
        )

    def url_count(self, *, user_id: int, task_id: int) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(URLResource)
                .where(URLResource.user_id == user_id, URLResource.task_id == task_id)
            )
            or 0
        )
