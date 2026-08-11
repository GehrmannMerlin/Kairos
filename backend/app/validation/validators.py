"""M-12 验证流水线前四层（D-014）：structure/type → required → evidence → business。

结构/类型复用 M-06/M-11 的 ExtractionSchemaValidator + normalize，不重复实现
第二套 schema parser（模块需求 12）。
"""

from __future__ import annotations

from typing import Any

from app.domain.spec import FieldSpec
from app.extraction.schema_validator import ExtractionSchemaValidator
from app.validation.contracts import ValidationIssue
from app.validation.policies import ValidationSettings


class StructureTypeValidator:
    """第一层：field exists + field type + enum + format（复用 M-06/M-11 validator）。"""

    def __init__(self, schema_validator: ExtractionSchemaValidator | None = None) -> None:
        self._schema = schema_validator or ExtractionSchemaValidator()

    def validate(self, record_values: dict, fields: list[FieldSpec]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        field_by_name = {f.name: f for f in fields}
        for name, value in record_values.items():
            if value in (None, ""):
                continue  # 缺失值归 required 层；结构层只管存在的值类型
            field = field_by_name.get(name)
            if field is None:
                issues.append(
                    ValidationIssue(
                        code="SCHEMA_UNKNOWN_FIELD",
                        field_name=name,
                        detail="字段不属于冻结 CollectionSpec",
                    )
                )
                continue
            issue = self._schema.validate(self._candidate(name, value), field)
            if issue is not None:
                issues.append(
                    ValidationIssue(
                        code=issue.code, field_name=name, detail=issue.detail, severity="error"
                    )
                )
        return issues

    @staticmethod
    def _candidate(name: str, value: Any):
        from app.extraction.contracts import (
            CandidateValidationStatus,
            ExtractionCandidate,
            ExtractorMethod,
        )

        return ExtractionCandidate(
            field_name=name,
            raw_value=str(value),
            normalized_value=None,
            value_type="text",
            method=ExtractorMethod.RULE,
            confidence=1.0,
            extractor_version="m12",
            validation_status=CandidateValidationStatus.VALID,
        )


class RequiredFieldValidator:
    """第二层：必填字段来自冻结 CollectionSpecVersion；缺失不直接 PASSED（语义分层）。"""

    def validate(self, record_values: dict, fields: list[FieldSpec]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field in fields:
            if not field.required:
                continue
            value = record_values.get(field.name)
            if value in (None, ""):
                issues.append(
                    ValidationIssue(
                        code="REQUIRED_FIELD_MISSING",
                        field_name=field.name,
                        detail=f"必填字段 {field.name} 缺失",
                    )
                )
        return issues


class EvidenceValidator:
    """第三层：有效业务字段进入 PASSED 必须存在合法 FieldEvidence（模块需求 14-15）。

    只对「实际填充值」的字段要求证据：可选字段缺失时由 required 层判定，无需证据。
    Evidence 必须：owner 一致、task/spec 一致、record/candidate 关联正确、
    snapshot/source 可追溯、method/version 存在。SYSTEM_DERIVED 例外显式审计。
    """

    def __init__(self, settings: ValidationSettings | None = None) -> None:
        self._settings = settings or ValidationSettings()

    def validate(
        self,
        record: Any,
        record_values: dict,
        evidence_by_field: dict[str, list],
        fields: list[FieldSpec],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field in fields:
            if record_values.get(field.name) in (None, ""):
                continue  # 字段未填充：由 required 层判定，无需证据
            evs = evidence_by_field.get(field.name) or []
            if evs:
                for ev in evs:
                    issue = self._check_chain(record, ev)
                    if issue is not None:
                        issues.append(issue)
                continue
            # 无证据字段：只有显式 SYSTEM_DERIVED 例外才允许
            if field.name in self._settings.system_derived_fields:
                continue
            issues.append(
                ValidationIssue(
                    code="EVIDENCE_MISSING",
                    field_name=field.name,
                    detail=f"字段 {field.name} 缺少 FieldEvidence",
                )
            )
        return issues

    def _check_chain(self, record: Any, ev: Any) -> ValidationIssue | None:
        if ev.user_id != record.user_id:
            return ValidationIssue(
                code="EVIDENCE_OWNER_MISMATCH",
                field_name=ev.field_name,
                detail="证据 user 归属不一致",
            )
        if ev.task_id not in (None, record.task_id):
            return ValidationIssue(
                code="EVIDENCE_TASK_MISMATCH",
                field_name=ev.field_name,
                detail="证据 task 关联不一致",
            )
        if ev.spec_version not in (None, record.spec_version):
            return ValidationIssue(
                code="EVIDENCE_SPEC_MISMATCH",
                field_name=ev.field_name,
                detail="证据 spec 版本不一致",
            )
        if ev.snapshot_id is None and ev.source_url in (None, ""):
            return ValidationIssue(
                code="EVIDENCE_NO_TRACE",
                field_name=ev.field_name,
                detail="证据缺少 snapshot/source 追溯",
            )
        if ev.extract_method in (None, "") or ev.extractor_version in (None, ""):
            return ValidationIssue(
                code="EVIDENCE_NO_METHOD",
                field_name=ev.field_name,
                detail="证据缺少 method/version",
            )
        return None


__all__ = ["StructureTypeValidator", "RequiredFieldValidator", "EvidenceValidator"]
