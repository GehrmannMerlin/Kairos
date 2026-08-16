"""M-12 persistence：ValidationResult / DedupeCluster / FieldConflict / QualitySnapshot /
CompletionDecision。所有 create() 只 flush（不 commit），executor 统一单事务提交（D-015）。

每条查询强制 user_id 边界；跨用户访问返回 M-02 NotFoundError（404），不泄漏存在性。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.auth.errors import NotFoundError
from app.domain.models import (
    CompletionDecision,
    DedupeCluster,
    FieldConflict,
    QualitySnapshot,
    ValidationResult,
)


def _owned(db: Any, model: type, user_id: int, obj_id: int) -> Any:
    row = db.get(model, obj_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("资源不存在")
    return row


class ValidationRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    # ---- ValidationResult ----
    def find_result(
        self, *, user_id: int, record_id: int, validation_version: str
    ) -> ValidationResult | None:
        return self._db.scalar(
            select(ValidationResult).where(
                ValidationResult.user_id == user_id,
                ValidationResult.record_id == record_id,
                ValidationResult.validation_version == validation_version,
            )
        )

    def create_result(
        self, *, user_id: int, task_id: int, run_id: int | None, spec_version: int, result: dict
    ) -> ValidationResult:
        data = dict(result)
        # spec_version_id 由参数写入；result dict 中如含同名字段则去重，避免重复 kwarg
        data.pop("spec_version_id", None)
        row = ValidationResult(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            spec_version_id=spec_version,
            **data,
        )
        self._db.add(row)
        return row

    def count_by_partition(self, *, user_id: int, task_id: int) -> dict[str, int]:
        from sqlalchemy import func

        rows = self._db.execute(
            select(ValidationResult.partition, func.count())
            .where(ValidationResult.user_id == user_id, ValidationResult.task_id == task_id)
            .group_by(ValidationResult.partition)
        ).all()
        return {p: int(c) for p, c in rows}

    # ---- DedupeCluster ----
    def find_group(
        self, *, user_id: int, task_id: int, business_key_fingerprint: str
    ) -> DedupeCluster | None:
        return self._db.scalar(
            select(DedupeCluster).where(
                DedupeCluster.user_id == user_id,
                DedupeCluster.task_id == task_id,
                DedupeCluster.business_key_fingerprint == business_key_fingerprint,
            )
        )

    def create_group(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int | None,
        spec_version: int,
        business_key: str,
        business_key_fingerprint: str,
        dedupe_policy_version: str,
        approximate: bool,
        record_ids: list[int],
    ) -> DedupeCluster:
        row = DedupeCluster(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            spec_version=spec_version,
            business_key=business_key,
            business_key_fingerprint=business_key_fingerprint,
            dedupe_policy_version=dedupe_policy_version,
            approximate=approximate,
            record_ids=record_ids,
        )
        self._db.add(row)
        self._db.flush()
        return row

    def list_groups(self, *, user_id: int, task_id: int) -> list[DedupeCluster]:
        return list(
            self._db.scalars(
                select(DedupeCluster).where(
                    DedupeCluster.user_id == user_id, DedupeCluster.task_id == task_id
                )
            )
        )

    # ---- FieldConflict ----
    def find_conflict(
        self, *, user_id: int, record_id: int, field_name: str, state: str
    ) -> FieldConflict | None:
        return self._db.scalar(
            select(FieldConflict).where(
                FieldConflict.user_id == user_id,
                FieldConflict.record_id == record_id,
                FieldConflict.field_name == field_name,
                FieldConflict.state == state,
            )
        )

    def create_conflict(
        self,
        *,
        user_id: int,
        task_id: int,
        record_id: int,
        dedupe_group_id: int | None,
        field_name: str,
        candidate_values: list,
        resolution: dict | None,
        state: str = "unresolved",
    ) -> FieldConflict:
        row = FieldConflict(
            user_id=user_id,
            task_id=task_id,
            record_id=record_id,
            dedupe_group_id=dedupe_group_id,
            field_name=field_name,
            candidate_values=candidate_values,
            resolution=resolution,
            state=state,
        )
        self._db.add(row)
        return row

    # ---- QualitySnapshot ----
    def create_snapshot(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int | None,
        spec_version: int,
        validation_version: str,
        dataset_version: str,
        sampling_policy_version: str,
        metrics: dict,
        denominators: dict,
        sample_refs: list,
    ) -> QualitySnapshot:
        row = QualitySnapshot(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            spec_version=spec_version,
            validation_version=validation_version,
            dataset_version=dataset_version,
            sampling_policy_version=sampling_policy_version,
            metrics=metrics,
            denominators=denominators,
            sample_refs=sample_refs,
        )
        self._db.add(row)
        return row

    def latest_snapshot(self, *, user_id: int, task_id: int) -> QualitySnapshot | None:
        return self._db.scalar(
            select(QualitySnapshot)
            .where(QualitySnapshot.user_id == user_id, QualitySnapshot.task_id == task_id)
            .order_by(QualitySnapshot.id.desc())
            .limit(1)
        )

    # ---- CompletionDecision ----
    def create_completion(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int | None,
        spec_version: int,
        plan_version: int,
        decision: dict,
    ) -> CompletionDecision:
        row = CompletionDecision(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            spec_version=spec_version,
            plan_version=plan_version,
            **decision,
        )
        self._db.add(row)
        return row

    def latest_completion(self, *, user_id: int, task_id: int) -> CompletionDecision | None:
        return self._db.scalar(
            select(CompletionDecision)
            .where(CompletionDecision.user_id == user_id, CompletionDecision.task_id == task_id)
            .order_by(CompletionDecision.id.desc())
            .limit(1)
        )


__all__ = ["ValidationRepository"]
