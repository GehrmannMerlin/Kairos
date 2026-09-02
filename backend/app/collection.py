from __future__ import annotations

import json
import math
from datetime import date
from hashlib import sha256
from typing import Any
from unicodedata import normalize as unicode_normalize

from pydantic import BaseModel, ConfigDict

from app.domain import (
    CollectionError,
    CollectionFieldType,
    CollectionSpecVersion,
    EvidenceSubmission,
    RecordStatus,
    RecordSubmission,
)
from app.url_policy import canonicalize_url, validate_public_http_url


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
    return " ".join(unicode_normalize("NFKC", value).split())


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_record_data(spec: CollectionSpecVersion, data_json: dict[str, Any]) -> dict[str, Any]:
    """Create a typed, deterministic representation without changing the raw extraction payload."""
    normalized: dict[str, Any] = {}
    fields = {field.name: field for field in spec.fields}
    for name, value in data_json.items():
        field = fields.get(name)
        if field is None or value is None:
            normalized[name] = value
        elif field.type in {CollectionFieldType.STRING, CollectionFieldType.URL} and isinstance(value, str):
            text = _normalized_text(value)
            if field.type is CollectionFieldType.URL:
                try:
                    text = canonicalize_url(text)
                except CollectionError:
                    pass
            normalized[name] = text
        elif field.type is CollectionFieldType.DATE and isinstance(value, str):
            try:
                normalized[name] = date.fromisoformat(value).isoformat()
            except ValueError:
                normalized[name] = _normalized_text(value)
        else:
            normalized[name] = value
    return normalized


def stable_record_fingerprint(normalized_data_json: dict[str, Any]) -> str:
    return sha256(_stable_json(normalized_data_json).encode("utf-8")).hexdigest()


def record_identity_key(spec: CollectionSpecVersion, normalized_data_json: dict[str, Any]) -> str:
    url_fields = [field for field in spec.fields if field.type is CollectionFieldType.URL]
    ordered_url_fields = sorted(url_fields, key=lambda field: (not field.required, spec.fields.index(field)))
    for field in ordered_url_fields:
        value = normalized_data_json.get(field.name)
        if not isinstance(value, str) or not value:
            continue
        try:
            canonicalize_url(value)
        except CollectionError:
            continue
        return f"{field.name}:{value}"

    required_fields = [field for field in spec.fields if field.required]
    if required_fields and all(
        _is_populated(normalized_data_json.get(field.name)) for field in required_fields
    ):
        values = [normalized_data_json[field.name] for field in required_fields]
        return f"required:{_stable_json(values)}"
    return f"fingerprint:{stable_record_fingerprint(normalized_data_json)}"


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
