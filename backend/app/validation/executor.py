"""M-12 DEDUPLICATE + VALIDATE 生产 executor（M-08 seam / 模块需求 55-56）。

副作用全部在 Activity；Workflow 只编排 typed refs。每个 batch 业务事务（Record 状态 +
ValidationResult + DedupeCluster + FieldConflict + DomainEvent）提交后才由 Workflow
commit_checkpoint（D-015）。同一 batch 重试不重复计数（幂等）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.activities.execution_seam import ExecuteUnitResult
from app.domain.models import Record, Run
from app.domain.repository import SpecVersionRepository
from app.domain.spec import FieldSpec
from app.extraction.executor_helpers import emit_event
from app.extraction.repository import ExtractionRepository
from app.validation.dedupe import (
    BusinessUniqueKeyStrategy,
    DedupeEngine,
    bounded_business_key,
)
from app.validation.pipeline import ValidationPipeline
from app.validation.policies import ValidationSettings
from app.validation.repository import ValidationRepository


class DeduplicateNodeExecutor:
    def __init__(
        self, db: Any, *, settings: ValidationSettings | None = None, max_batch: int = 200
    ) -> None:
        self._db = db
        self._settings = settings or ValidationSettings()
        self._max_batch = max_batch

    async def execute(self, unit) -> ExecuteUnitResult:
        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(unit.index, {}, status="FAILED", error_code="RUN_NOT_FOUND")
        spec = SpecVersionRepository(self._db).get_version(
            run.user_id, run.task_id, run.spec_version
        )
        records = ExtractionRepository(self._db).records_for_task(run.user_id, run.task_id)
        records = records[: self._max_batch]
        if not records:
            return ExecuteUnitResult(
                unit.index, {"dedupe_groups": 0, "run_id": run.id}, status="OK"
            )
        policy = BusinessUniqueKeyStrategy().resolve(spec.payload)
        engine = DedupeEngine(self._settings)
        fields = [FieldSpec.model_validate(f) for f in (spec.payload.get("fields") or [])]
        groups, _ = engine.group(records, policy, fields)

        repo = ValidationRepository(self._db)
        group_rows = []
        for g in groups:
            fp = g["business_key_fingerprint"]
            existing = repo.find_group(
                user_id=run.user_id, task_id=run.task_id, business_key_fingerprint=fp
            )
            if existing is not None:
                group_rows.append(existing)
                continue
            row = repo.create_group(
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                spec_version=run.spec_version,
                business_key=bounded_business_key(g["business_key"]),
                business_key_fingerprint=fp,
                dedupe_policy_version=self._settings.validation_version,
                approximate=g["approximate"],
                record_ids=g["record_ids"],
            )
            group_rows.append(row)
            # 回写 Record.business_key（供 M-13 查询/审计）
            for rid in g["record_ids"]:
                rec = self._db.get(Record, rid)
                if rec is not None and rec.business_key is None:
                    rec.business_key = bounded_business_key(g["business_key"])
                    self._db.add(rec)
        emit_event(
            self._db,
            run,
            "validation.dedupe_completed",
            {"groups": len(group_rows), "records": len(records)},
        )
        self._db.commit()
        return ExecuteUnitResult(
            unit.index,
            {
                "dedupe_groups": len(group_rows),
                "dedupe_group_ids": [r.id for r in group_rows],
                "run_id": run.id,
            },
            status="OK",
        )


class ValidateNodeExecutor:
    def __init__(
        self, db: Any, *, settings: ValidationSettings | None = None, max_batch: int = 50
    ) -> None:
        self._db = db
        self._settings = settings or ValidationSettings()
        self._max_batch = max_batch

    def _is_validated(self, record: Record) -> bool:
        # 幂等：同一 validation_version 已有 ValidationResult 即视为已验证（不重跑）
        existing = ValidationRepository(self._db).find_result(
            user_id=record.user_id,
            record_id=record.id,
            validation_version=self._settings.validation_version,
        )
        return existing is not None

    async def execute(self, unit) -> ExecuteUnitResult:
        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(unit.index, {}, status="FAILED", error_code="RUN_NOT_FOUND")
        spec = SpecVersionRepository(self._db).get_version(
            run.user_id, run.task_id, run.spec_version
        )
        candidates = ExtractionRepository(self._db).records_for_task(run.user_id, run.task_id)
        records = [r for r in candidates if not self._is_validated(r)][: self._max_batch]
        emit_event(self._db, run, "validation.started", {"records": len(records)})
        pipeline = ValidationPipeline(self._settings)
        repo = ValidationRepository(self._db)
        validated = 0
        for record in records:
            result = pipeline.run(self._db, record, spec.payload, run=run)
            repo.create_result(
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                spec_version=run.spec_version,
                result=result,
            )
            record.partition = result["partition"]
            record.review_type = result["review_type"]
            record.review_reason = result["review_reason"]
            record.validated_at = datetime.now(UTC)
            self._db.add(record)
            validated += 1
        counts = repo.count_by_partition(user_id=run.user_id, task_id=run.task_id)
        emit_event(
            self._db,
            run,
            "validation.completed",
            {"validated": validated, **counts},
        )
        self._db.commit()
        return ExecuteUnitResult(
            unit.index,
            {"validated": validated, **counts, "run_id": run.id},
            status="OK",
        )


__all__ = ["DeduplicateNodeExecutor", "ValidateNodeExecutor"]
