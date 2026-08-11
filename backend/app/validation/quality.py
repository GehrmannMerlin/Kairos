"""M-12 QualityMetrics：全部来自数据库事实聚合（模块需求 39-42），denominator 明确。

pass_rate = PASSED / total validated records；missing_rate = 缺失必填 record / total；
duplicate_rate = possible_duplicate review / total；conflict_count = 未裁决冲突数；
source_coverage = 产生 Record 的来源 / 应覆盖来源（M-09 source/discovery 口径）；
sampling_accuracy = 已知正确答案 sample 命中率（自动 fixture 已知答案时计算）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class QualityMetrics(BaseModel):
    model_config = _STRICT

    pass_rate: float
    missing_rate: float
    duplicate_rate: float
    conflict_count: int
    source_coverage: float
    sampling_accuracy: float | None
    needs_review_count: int
    rejected_count: int
    denominators: dict


class QualityMetricsService:
    def compute(
        self,
        db: Any,
        *,
        user_id: int,
        task_id: int,
        run_id: int | None,
        spec_version: int,
        validation_version: str,
        dataset_version: str,
        sampling_policy_version: str,
        sample_refs: list[dict],
        known_answers: dict[int, dict] | None = None,
    ) -> dict:
        from sqlalchemy import func, select

        from app.domain.models import FieldConflict, ValidationResult
        from app.validation.repository import ValidationRepository

        repo = ValidationRepository(db)
        counts = repo.count_by_partition(user_id=user_id, task_id=task_id)
        passed = counts.get("passed", 0)
        review = counts.get("needs_review", 0)
        rejected = counts.get("rejected", 0)
        total = passed + review + rejected

        missing = int(
            db.scalar(
                select(func.count())
                .select_from(ValidationResult)
                .where(
                    ValidationResult.user_id == user_id,
                    ValidationResult.task_id == task_id,
                    ValidationResult.review_type == "missing_required",
                )
            )
            or 0
        )
        duplicate = int(
            db.scalar(
                select(func.count())
                .select_from(ValidationResult)
                .where(
                    ValidationResult.user_id == user_id,
                    ValidationResult.task_id == task_id,
                    ValidationResult.review_type == "possible_duplicate",
                )
            )
            or 0
        )
        conflict = int(
            db.scalar(
                select(func.count())
                .select_from(FieldConflict)
                .where(
                    FieldConflict.user_id == user_id,
                    FieldConflict.task_id == task_id,
                    FieldConflict.state == "unresolved",
                )
            )
            or 0
        )

        eligible = self._source_facts(db, user_id, task_id)
        covered = self._covered_sources(db, user_id, task_id)
        denominators = {
            "total_validated_records": total,
            "eligible_sources": max(1, len(eligible)),
            "covered_sources": max(1, len(covered)),
        }

        sampling_accuracy = None
        if known_answers:
            hits = sum(1 for ref in sample_refs if ref["record_id"] in known_answers)
            sampling_accuracy = round(hits / len(sample_refs), 4) if sample_refs else 0.0

        metrics = QualityMetrics(
            pass_rate=round(passed / total, 4) if total else 0.0,
            missing_rate=round(missing / total, 4) if total else 0.0,
            duplicate_rate=round(duplicate / total, 4) if total else 0.0,
            conflict_count=conflict,
            source_coverage=round(
                denominators["covered_sources"] / denominators["eligible_sources"], 4
            ),
            sampling_accuracy=sampling_accuracy,
            needs_review_count=review,
            rejected_count=rejected,
            denominators=denominators,
        )
        return {
            "metrics": metrics.model_dump(),
            "denominators": denominators,
            "dataset_version": dataset_version,
            "sampling_policy_version": sampling_policy_version,
        }

    def _source_facts(self, db, user_id: int, task_id: int) -> list[str]:
        from sqlalchemy import select

        from app.domain.models import URLResource

        return list(
            db.scalars(
                select(URLResource.source_type)
                .where(URLResource.user_id == user_id, URLResource.task_id == task_id)
                .distinct()
            )
        )

    def _covered_sources(self, db, user_id: int, task_id: int) -> list[str]:
        from sqlalchemy import select

        from app.domain.models import URLResource

        return list(
            db.scalars(
                select(URLResource.source_type)
                .where(
                    URLResource.user_id == user_id,
                    URLResource.task_id == task_id,
                    URLResource.status.in_(["FETCHED", "HANDED_OFF"]),
                )
                .distinct()
            )
        )


__all__ = ["QualityMetrics", "QualityMetricsService"]
