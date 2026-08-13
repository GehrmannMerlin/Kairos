"""统一 ExtractionSchemaValidator：LLM 与 Rule Extractor 无特殊通道（D-010 校验边界）。

校验 field name / field type / enum / format（URL/EMAIL/NUMBER/PHONE/DATE）。全部
extractor 输出进入同一 Validator，绝不绕过（十六）。
"""

from __future__ import annotations

from app.domain.spec import FieldSpec, FieldType
from app.extraction.contracts import ExtractionCandidate, ExtractionIssue
from app.extraction.normalize import (
    normalize_boolean,
    normalize_date,
    normalize_email,
    normalize_number,
    normalize_phone,
    normalize_url,
)


class ExtractionSchemaValidator:
    def validate(self, candidate: ExtractionCandidate, field: FieldSpec) -> ExtractionIssue | None:
        if candidate.field_name != field.name:
            return ExtractionIssue(
                code="SCHEMA_UNKNOWN_FIELD",
                field_name=candidate.field_name,
                detail="字段名不属于当前冻结 CollectionSpec",
                method=candidate.method,
            )
        if not self._value_valid(candidate.raw_value or "", field.type):
            return ExtractionIssue(
                code=f"SCHEMA_TYPE_{field.type.value.upper()}",
                field_name=field.name,
                detail=f"值不符合字段类型 {field.type.value}",
                method=candidate.method,
            )
        return None

    def _value_valid(self, raw: str, field_type: FieldType) -> bool:
        if not raw.strip():
            return False
        if field_type == FieldType.URL:
            return normalize_url(raw) is not None
        if field_type == FieldType.EMAIL:
            return normalize_email(raw) is not None
        if field_type == FieldType.NUMBER:
            return normalize_number(raw) is not None
        if field_type == FieldType.PHONE:
            return normalize_phone(raw) is not None
        if field_type == FieldType.BOOLEAN:
            return normalize_boolean(raw) is not None
        if field_type == FieldType.DATE:
            return normalize_date(raw) is not None
        return True  # TEXT / OTHER: any non-empty value
