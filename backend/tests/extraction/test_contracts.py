"""M-11 typed extraction contracts."""
from __future__ import annotations

from app.extraction.contracts import (
    CandidateValidationStatus,
    ExtractionCandidate,
    ExtractionResult,
    ExtractorMethod,
    RecordPartition,
)


def test_extraction_candidate_strict():
    c = ExtractionCandidate(
        field_name="官网",
        raw_value="https://example.com",
        method=ExtractorMethod.JSON_LD,
        confidence=0.95,
        extractor_version="m11.1",
        validation_status=CandidateValidationStatus.VALID,
    )
    assert c.method == ExtractorMethod.JSON_LD
    assert c.normalized_value is None
    assert c.rule_version is None


def test_extraction_candidate_forbids_extra_keys():
    from pydantic import ValidationError

    try:
        ExtractionCandidate(
            field_name="x",
            raw_value="y",
            method=ExtractorMethod.META,
            confidence=0.9,
            extractor_version="m11.1",
            unexpected="nope",
        )
    except ValidationError:
        return
    raise AssertionError("extra keys must be rejected")


def test_extraction_result_defaults():
    r = ExtractionResult(
        snapshot_id=1, schema_version="m11.1", extractor_type="json_ld", extractor_version="m11.1"
    )
    assert r.candidates == []
    assert r.unresolved_fields == []
    assert r.issues == []


def test_record_partition_extracted():
    assert RecordPartition.EXTRACTED.value == "extracted"
