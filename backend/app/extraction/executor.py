"""M-08 EXTRACT / NORMALIZE 节点真实执行器（M-11）。

EXTRACT：小批次消费 immutable PageSnapshot（extract_batch_size 个/次）→ 提取阶梯 →
每个快照独立事务写入 Record(EXTRACTED) + FieldEvidence + DomainEvent；剩余快照返回
MORE_PENDING 由 Workflow 重取同一单元继续。NORMALIZE：只做字段级 canonicalization，
绝不业务去重/冲突裁决（四十五）。
"""

from __future__ import annotations

import asyncio
from time import perf_counter
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
        self._resolved_model_audit: dict = {}

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
        # M-11 小批次（D-015）：单次 Activity 最多处理 extract_batch_size 个快照，每个快照
        # 独立事务提交。剩余快照通过 MORE_PENDING → Workflow 重取同一单元继续。
        limit = min(self._max_batch, self._settings.extract_batch_size)
        pending = repo.pending_snapshots(user_id=run.user_id, task_id=run.task_id, limit=limit)
        if not pending:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="OK",
                committed_refs={
                    "extracted": 0,
                    "failed": 0,
                    "remaining": 0,
                    "run_id": run.id,
                    "node_id": unit.node_id,
                    "node_type": unit.node_type,
                },
            )

        resolved, api_key, audit = self._resolve_model(run)
        self._resolved_model_audit = audit
        pipeline = self._pipeline or self._build_pipeline(run, resolved, api_key, audit)
        emit_event(self._db, run, "extraction.started", {"snapshots": len(pending)})
        # started 事件独立提交，保证取消/预算中断时进度信号仍可见（不再是“最后一次性提交”）。
        self._db.commit()

        started = perf_counter()
        budget = self._settings.extract_activity_budget_seconds
        extracted = 0
        failed = 0
        for snapshot in pending:
            # 预算检查在每个快照开始前：预算(100s)+最坏单快照(90s) < Activity timeout(200s)，
            # 保证本 Activity 在 start_to_close 前正常返回（MORE_PENDING），而不是被 Temporal 取消。
            if perf_counter() - started > budget:
                break
            try:
                result = await pipeline.run(snapshot, spec.payload, user_id=run.user_id)
            except asyncio.CancelledError:
                # 真实取消（BaseException）向上传播，绝不吞成 ProviderTimeout/普通失败；
                # 此前已提交的快照不丢，未提交部分随 session 回滚。D-013 取消永不重试。
                raise
            except Exception as exc:
                # 单快照失败局部化（D-013）；失败账本防止 MORE_PENDING 重跑无限循环。
                failed += 1
                repo.mark_snapshot_extraction_failed(snapshot.id)
                emit_event(
                    self._db,
                    run,
                    "extraction.failed",
                    {"snapshot_id": snapshot.id, "error": str(exc)[:200]},
                )
                self._db.commit()
                continue
            page_llm = int((result.technical_metadata or {}).get("llm_invocations", 0))
            if not result.candidates:
                # 全阶梯（结构化/规则/LLM 缩小重试）后仍无候选：页面级合法失败，记录账本。
                failed += 1
                repo.mark_snapshot_extraction_failed(snapshot.id)
                emit_event(
                    self._db,
                    run,
                    "extraction.failed",
                    {
                        "snapshot_id": snapshot.id,
                        "error": "no candidates after extraction ladder",
                        "unresolved_fields": list(result.unresolved_fields)[:5],
                    },
                )
                self._db.commit()
                continue
            if page_llm > 0:
                # 真实模型调用（安全摘要：仅 provider/model，绝无 secret/prompt）。
                emit_event(
                    self._db,
                    run,
                    "extraction.llm_fallback_used",
                    {
                        "snapshot_id": snapshot.id,
                        "provider": self._resolved_model_audit.get("provider"),
                        "model": self._resolved_model_audit.get("model"),
                    },
                )
            record = self._persist(run, snapshot, result)
            extracted += 1
            emit_event(
                self._db,
                run,
                "extraction.completed",
                {"snapshot_id": snapshot.id, "record_id": record.id},
            )
            self._db.commit()

        remaining = len(repo.pending_snapshots(user_id=run.user_id, task_id=run.task_id))
        first_snapshot_id = pending[0].id if pending else 0
        # 批次身份唯一（首快照 id 单调递增），供 Workflow commit_checkpoint 区分各小批。
        batch_identity = f"extract-{run.id}-{unit.index}-{first_snapshot_id}"
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="MORE_PENDING" if remaining > 0 else "OK",
            committed_refs={
                "extracted": extracted,
                "failed": failed,
                "remaining": remaining,
                "batch_identity": batch_identity,
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
                "snapshot_ids": [s.id for s in pending],
            },
        )

    def _resolve_model(self, run: Run) -> tuple[Any, Any, dict]:
        """Resolve the run's frozen model for the live-activity audit (never a secret)."""
        if self._model_resolver is None:
            return None, None, {}
        try:
            resolved, api_key, audit = self._model_resolver.resolve_for_run(run)
        except Exception:
            return None, None, {}
        return resolved, api_key, audit or {}

    def _build_pipeline(
        self, run: Run, resolved: Any, api_key: Any, audit: dict
    ) -> ExtractionPipeline:
        from app.extraction.llm import SemanticExtractionAgent

        agent = SemanticExtractionAgent(settings=self._settings)
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
            payload = dict(record.payload or {})  # 新 dict，确保 ORM 检测到 JSON 变更
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
