"""Unified ExtractionSchemaValidator (LLM and rules have no bypass)."""
from __future__ import annotations

from app.domain.spec import FieldSpec, FieldType
from app.extraction.contracts import ExtractionCandidate, ExtractorMethod
from app.extraction.normalize import normalize_value
from app.extraction.schema_validator import ExtractionSchemaValidator


def _cand(field_name: str, raw: str, method: ExtractorMethod = ExtractorMethod.LLM):
    return ExtractionCandidate(
        field_name=field_name,
        raw_value=raw,
        method=method,
        confidence=0.9,
        extractor_version="m11.1",
    )


def test_valid_url_passes():
    f = FieldSpec(name="官网", type=FieldType.URL)
    assert ExtractionSchemaValidator().validate(_cand("官网", "https://example.com"), f) is None


def test_bad_url_rejected():
    f = FieldSpec(name="官网", type=FieldType.URL)
    issue = ExtractionSchemaValidator().validate(_cand("官网", "not a url"), f)
    assert issue is not None
    assert issue.code == "SCHEMA_TYPE_URL"


def test_unknown_field_rejected():
    f = FieldSpec(name="官网", type=FieldType.URL)
    issue = ExtractionSchemaValidator().validate(_cand("不存在字段", "https://a.com"), f)
    assert issue is not None
    assert issue.code == "SCHEMA_UNKNOWN_FIELD"


def test_email_and_phone():
    email_f = FieldSpec(name="邮箱", type=FieldType.EMAIL)
    assert ExtractionSchemaValidator().validate(_cand("邮箱", "a@b.com"), email_f) is None
    assert ExtractionSchemaValidator().validate(_cand("邮箱", "not-an-email"), email_f) is not None
    phone_f = FieldSpec(name="电话", type=FieldType.PHONE)
    assert ExtractionSchemaValidator().validate(_cand("电话", "0755-12345678"), phone_f) is None


def test_normalize_url_email_phone():
    assert normalize_value("  https://EXAMPLE.com/path  ", FieldType.URL) == "https://example.com/path"
    assert normalize_value("  A@B.COM  ", FieldType.EMAIL) == "a@b.com"
    assert normalize_value("(0755) 1234-5678", FieldType.PHONE) == "075512345678"
    assert normalize_value("  深圳测试公司  ", FieldType.TEXT) == "深圳测试公司"
    assert normalize_value("是", FieldType.BOOLEAN) == "true"
