from __future__ import annotations

import math
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain import (
    CollectionError,
    CollectionFieldType,
    CollectionSpecVersion,
    EvidenceSubmission,
    RecordStatus,
    RecordSubmission,
)
from app.url_policy import validate_public_http_url


class ValidatedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    quote: str
    confidence: float | None = None
    verified: bool


class ValidatedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_json: dict[str, Any]
    status: RecordStatus
    validation_issues: list[str]
    evidence: list[ValidatedEvidence]


def _is_populated(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _matches_type(value: Any, field_type: CollectionFieldType) -> bool:
    if field_type is CollectionFieldType.STRING:
        return type(value) is str
    if field_type is CollectionFieldType.INTEGER:
        return type(value) is int
    if field_type is CollectionFieldType.NUMBER:
        return type(value) in {int, float} and math.isfinite(float(value))
    if field_type is CollectionFieldType.BOOLEAN:
        return type(value) is bool
    if field_type is CollectionFieldType.DATE:
        if type(value) is not str:
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if field_type is CollectionFieldType.URL:
        if type(value) is not str:
            return False
        try:
            validate_public_http_url(value)
        except CollectionError:
            return False
        return True
    return False


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def validate_record_submission(
    spec: CollectionSpecVersion,
    snapshot_text: str,
    submission: RecordSubmission,
) -> ValidatedRecord:
    known_fields = {field.name: field for field in spec.fields}
    submitted_names = set(submission.fields) | set(submission.evidence)
    unknown_fields = sorted(submitted_names - known_fields.keys())
    if unknown_fields:
        raise CollectionError("UNKNOWN_FIELD", f"unknown collection field: {unknown_fields[0]}")

    data_json = dict(submission.fields)
    issues: list[str] = []
    evidence: list[ValidatedEvidence] = []
    meaningful_count = 0
    normalized_snapshot = _normalized_text(snapshot_text)

    for field in spec.fields:
        value = data_json.get(field.name)
        populated = _is_populated(value)
        if populated:
            meaningful_count += 1
            if not _matches_type(value, field.type):
                issues.append("INVALID_FIELD_TYPE")
            evidence_submission: EvidenceSubmission | None = submission.evidence.get(field.name)
            if evidence_submission is None:
                issues.append("EVIDENCE_MISSING")
                continue
            quote = _normalized_text(evidence_submission.quote)
            verified = bool(quote) and quote in normalized_snapshot
            evidence.append(
                ValidatedEvidence(
                    field_name=field.name,
                    quote=evidence_submission.quote[:1000],
                    confidence=evidence_submission.confidence,
                    verified=verified,
                )
            )
            if not verified:
                issues.append("EVIDENCE_QUOTE_NOT_FOUND")
        elif field.required:
            issues.append("MISSING_REQUIRED_FIELD")

    if meaningful_count == 0:
        return ValidatedRecord(
            data_json=data_json,
            status=RecordStatus.REJECTED,
            validation_issues=["EMPTY_RECORD", *issues],
            evidence=evidence,
        )
    return ValidatedRecord(
        data_json=data_json,
        status=RecordStatus.NEEDS_REVIEW if issues else RecordStatus.PASSED,
        validation_issues=issues,
        evidence=evidence,
    )
