"""M-08 EXTRACT / NORMALIZE 节点真实执行器（M-11）。

EXTRACT：消费 immutable PageSnapshot → 提取阶梯 → 单事务写入 Record(EXTRACTED) +
FieldEvidence + DomainEvent → committed_refs。NORMALIZE：只做字段级 canonicalization，
绝不业务去重/冲突裁决（四十五）。
"""

from __future__ import annotations

from typing import Any

from app.activities.execution_seam import ExecuteUnitResult
from app.domain.idempotency import stable_fingerprint
from app.domain.models import PageSnapshot, Record, Run
from app.domain.repository import SpecVersionRepository
from app.domain.spec import FieldSpec
from app.extraction.contracts import ExtractionSettings
from app.extraction.executor_helpers import emit_event
from app.extraction.normalize import normalize_value
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.repository import (
    ExtractionRepository,
    FieldEvidenceRepository,
)
from app.infra.object_storage import ObjectStorage


class ExtractNodeExecutor:
    def __init__(
        self,
        db: Any,
        storage: ObjectStorage,
        *,
        pipeline: ExtractionPipeline | None = None,
        model_resolver: Any = None,
        settings: ExtractionSettings | None = None,
        max_batch: int = 50,
    ) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings or ExtractionSettings()
        self._pipeline = pipeline
        self._model_resolver = model_resolver
        self._max_batch = max_batch

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
        repo = ExtractionRepository(self._db)
        pending = repo.pending_snapshots(
            user_id=run.user_id, task_id=run.task_id, limit=self._max_batch
        )
        if not pending:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="OK",
                committed_refs={
                    "extracted": 0,
                    "run_id": run.id,
                    "node_id": unit.node_id,
                    "node_type": unit.node_type,
                },
            )

        pipeline = self._pipeline or self._build_pipeline(run)
        emit_event(self._db, run, "extraction.started", {"snapshots": len(pending)})
        extracted = 0
        for snapshot in pending:
            try:
                result = await pipeline.run(snapshot, spec.payload, user_id=run.user_id)
            except Exception as exc:  # 单快照失败不阻塞批次（D-013 失败隔离）
                emit_event(
                    self._db,
                    run,
                    "extraction.failed",
                    {"snapshot_id": snapshot.id, "error": str(exc)[:200]},
                )
                continue
            if not result.candidates:
                continue
            record = self._persist(run, snapshot, result)
            extracted += 1
            emit_event(
                self._db,
                run,
                "extraction.completed",
                {"snapshot_id": snapshot.id, "record_id": record.id},
            )
        self._db.commit()
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "extracted": extracted,
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
                "snapshot_ids": [s.id for s in pending],
            },
        )

    def _build_pipeline(self, run: Run) -> ExtractionPipeline:
        from app.extraction.llm import SemanticExtractionAgent

        agent = SemanticExtractionAgent(settings=self._settings)
        audit: dict = {}
        if self._model_resolver is not None:
            resolved, api_key, audit = self._model_resolver.resolve_for_run(run)
            if resolved is not None:
                agent.bind_model(resolved, api_key)
        return ExtractionPipeline(
            self._db, self._storage, llm_agent=agent, settings=self._settings, model_audit=audit
        )

    def _persist(self, run: Run, snapshot: PageSnapshot, result) -> Record:
        repo = ExtractionRepository(self._db)
        values = {
            c.field_name: c.normalized_value if c.normalized_value is not None else c.raw_value
            for c in result.candidates
        }
        payload = {
            "values": values,
            "snapshot_id": snapshot.id,
            "spec_version": run.spec_version,
            "url": snapshot.final_url or "",
            "unresolved_fields": result.unresolved_fields,
            "issues": [i.model_dump(mode="json") for i in result.issues],
            "rule_versions": {
                c.field_name: c.rule_version
                for c in result.candidates
                if c.rule_version is not None
            },
        }
        record = repo.create_record(
            user_id=run.user_id,
            task_id=run.task_id,
            run_id=run.id,
            spec_version=run.spec_version,
            url_resource_id=snapshot.url_resource_id,
            payload=payload,
        )
        record.content_hash = stable_fingerprint(
            "record", snapshot.content_hash, run.spec_version, sorted(values.items())
        )
        ev_repo = FieldEvidenceRepository(self._db)
        for c in result.candidates:
            evidence = ev_repo.create(
                record_id=record.id,
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                spec_version=run.spec_version,
                snapshot_id=snapshot.id,
                url_resource_id=snapshot.url_resource_id,
                field_name=c.field_name,
                value=c.raw_value,
                normalized_value=c.normalized_value or c.raw_value,
                value_type=c.value_type,
                source_url=snapshot.final_url or "",
                source_locator=c.source_locator,
                raw_snippet=c.raw_snippet or "",
                extract_method=c.method.value,
                extractor_version=c.extractor_version,
                rule_version_id=c.rule_version,
                model_config_id=c.model_config_id,
                confidence=c.confidence,
                evidence_hash=stable_fingerprint(
                    "evidence",
                    snapshot.id,
                    c.field_name,
                    c.method.value,
                    c.raw_value,
                    c.source_locator,
                ),
                validation_status=c.validation_status.value,
                issue_code=c.issue_code,
            )
            c.evidence_ref = evidence.id
        return record


class NormalizeNodeExecutor:
    def __init__(self, db: Any, *, settings: ExtractionSettings | None = None) -> None:
        self._db = db
        self._settings = settings or ExtractionSettings()

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
        fields = self._parse_fields(spec.payload)
        repo = ExtractionRepository(self._db)
        normalized_count = 0
        for record in repo.records_for_task(run.user_id, run.task_id):
            payload = record.payload or {}
            values = dict(payload.get("values") or {})
            changed = False
            for field in fields:
                raw = values.get(field.name)
                if raw is None:
                    continue
                canonical = normalize_value(str(raw), field.type)
                if canonical is not None and canonical != raw:
                    values[field.name] = canonical
                    changed = True
            if changed:
                payload["values"] = values
                record.payload = payload
                self._db.add(record)
                normalized_count += 1
        self._db.commit()
        emit_event(self._db, run, "normalize.completed", {"normalized": normalized_count})
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "normalized": normalized_count,
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
            },
        )

    @staticmethod
    def _parse_fields(spec_payload: dict) -> list[FieldSpec]:
        out: list[FieldSpec] = []
        for f in spec_payload.get("fields") or []:
            try:
                out.append(FieldSpec.model_validate(f))
            except Exception:
                continue
        return out
