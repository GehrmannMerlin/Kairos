"""M-12 canonical validation pipeline（D-014 顺序固定，不可随意调整）。

Extraction Candidate → structure/type → required → evidence → business →
dedupe → conflict → partition。后一步可依赖前一步确定事实。

dedupe 语义：record 无 business key（无法 exact 归组）→ dedupe_unresolved=True →
NEEDS_REVIEW；exact/approximate 归组视为 resolved，组内字段冲突交给 conflict 层。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.domain.models import FieldEvidence, Record
from app.domain.spec import FieldSpec, validate_spec_payload
from app.validation.business_rules import BusinessRuleValidator
from app.validation.conflict import ConflictCandidateValue, ConflictResolver
from app.validation.dedupe import BusinessUniqueKeyStrategy, DedupeEngine
from app.validation.partitioner import Partitioner
from app.validation.policies import ValidationSettings
from app.validation.validators import (
    EvidenceValidator,
    RequiredFieldValidator,
    StructureTypeValidator,
)


class ValidationPipeline:
    def __init__(self, settings: ValidationSettings | None = None) -> None:
        self._settings = settings or ValidationSettings()
        self._partitioner = Partitioner()

    def run(self, db: Any, record: Any, spec_payload: dict, *, run: Any) -> dict:
        spec = validate_spec_payload(spec_payload)
        fields = [FieldSpec.model_validate(f.model_dump()) for f in spec.fields]
        values = (record.payload or {}).get("values") or {}

        structural = StructureTypeValidator().validate(values, fields)
        required = RequiredFieldValidator().validate(values, fields)
        evidence_by_field = self._evidence_by_field(db, record.id)
        evidence = EvidenceValidator(self._settings).validate(
            record, values, evidence_by_field, fields
        )
        business = BusinessRuleValidator().validate(values, self._business_rules(spec_payload))

        # dedupe：当前 task 所有 EXTRACTED 候选 → 同一 business key 归组
        from app.extraction.repository import ExtractionRepository

        policy = BusinessUniqueKeyStrategy().resolve(spec_payload)
        engine = DedupeEngine(self._settings)
        candidates = ExtractionRepository(db).records_for_task(record.user_id, record.task_id)
        groups, _ = engine.group(candidates, policy, fields)
        group = next((g for g in groups if record.id in g["record_ids"]), None)
        dedupe_unresolved = group is None  # 无 business key → 无法去重 → NEEDS_REVIEW
        dedupe_result = group or {"record_ids": [record.id], "approximate": False}

        # conflict：组内同字段不同值 → ConflictResolver（不可裁决 → NEEDS_REVIEW）
        conflict_unresolved = False
        conflict_result: dict = {}
        if group is not None and len(group["record_ids"]) > 1:
            conflict_result, conflict_unresolved = self._resolve_conflicts(
                db, record, group, fields
            )

        decision = self._partitioner.decide(
            structural=structural,
            required=required,
            evidence=evidence,
            business=business,
            dedupe_unresolved=dedupe_unresolved,
            conflict_unresolved=conflict_unresolved,
        )
        return {
            "record_id": record.id,
            "spec_version_id": record.spec_version,
            "validation_version": self._settings.validation_version,
            "structural_issues": [i.model_dump(mode="json") for i in structural],
            "required_field_issues": [i.model_dump(mode="json") for i in required],
            "evidence_issues": [i.model_dump(mode="json") for i in evidence],
            "business_rule_issues": [i.model_dump(mode="json") for i in business],
            "dedupe_group_id": None,  # 由 executor 持久化 DedupeCluster 后回填
            "dedupe_result": dedupe_result,
            "conflict_result": conflict_result,
            "partition": decision.partition.value,
            "review_type": decision.review_type,
            "review_reason": decision.review_reason.value if decision.review_reason else None,
            "allowed_actions": decision.allowed_actions,
            "quality_contribution": decision.quality_contribution,
            "validated_at": datetime.now(UTC),
        }

    def _evidence_by_field(self, db: Any, record_id: int) -> dict[str, list]:
        rows = db.scalars(select(FieldEvidence).where(FieldEvidence.record_id == record_id)).all()
        out: dict[str, list] = {}
        for ev in rows:
            out.setdefault(ev.field_name, []).append(ev)
        return out

    def _business_rules(self, spec_payload: dict) -> list:
        from app.validation.business_rules import BusinessValidationRule

        rules = spec_payload.get("business_rules") or []
        out = []
        for r in rules:
            try:
                out.append(BusinessValidationRule.model_validate(r))
            except Exception:
                continue
        return out

    def _resolve_conflicts(
        self, db: Any, record: Any, group: dict, fields: list[FieldSpec]
    ) -> tuple[dict, bool]:
        from app.extraction.repository import FieldEvidenceRepository

        resolver = ConflictResolver()
        any_unresolved = False
        result: dict = {"decisions": {}}
        # 组内所有记录按字段聚合候选值（保留 Evidence 链，不删除任何候选）
        by_field: dict[str, list] = {}
        for rid in group["record_ids"]:
            row = db.get(Record, rid)
            if row is None:
                continue
            values = (row.payload or {}).get("values") or {}
            evs = FieldEvidenceRepository(db).list_for_record(record.user_id, rid)
            ev_by_name = {e.field_name: e for e in evs}
            for name, value in values.items():
                if value in (None, ""):
                    continue
                ev = ev_by_name.get(name)
                method = ev.extract_method if ev and ev.extract_method else "llm"
                by_field.setdefault(name, []).append(
                    ConflictCandidateValue(
                        record_id=rid,
                        value=str(value),
                        evidence_strength=(ev.confidence or 0.5) if ev else 0.0,
                        source_priority=60,
                        method=method,
                        rule_validated=(ev.validation_status == "valid" if ev else False),
                        confidence=(ev.confidence or 0.0) if ev else 0.0,
                    )
                )
        for name, cands in by_field.items():
            if len({c.value for c in cands}) <= 1:
                continue
            res = resolver.resolve(name, cands)
            result["decisions"][name] = res.model_dump(mode="json")
            if res.decision == "needs_review":
                any_unresolved = True
        return result, any_unresolved


__all__ = ["ValidationPipeline"]
