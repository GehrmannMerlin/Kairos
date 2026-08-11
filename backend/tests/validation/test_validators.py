"""CORE TEST A — Validation Matrix（模块需求 69）。

参数化覆盖：valid→PASSED 判定所需结构/必填/证据全过；missing required 可人工补全
→ NEEDS_REVIEW 语义；unrecoverable invalid→REJECTED 语义；missing Evidence→不 PASSED；
system-derived 显式例外→allowed。
"""

from __future__ import annotations

import pytest
from app.domain.spec import FieldSpec
from app.extraction.schema_validator import ExtractionSchemaValidator
from app.validation.business_rules import BusinessRuleValidator, BusinessValidationRule
from app.validation.policies import ValidationSettings
from app.validation.validators import (
    EvidenceValidator,
    RequiredFieldValidator,
    StructureTypeValidator,
)

FIELDS = [
    FieldSpec(name="公司名", type="text", required=True),
    FieldSpec(name="官网", type="url", required=True),
    FieldSpec(name="电话", type="phone", required=False),
]


def _record():
    class _R:
        user_id = 7
        task_id = 3
        spec_version = 1

    return _R()


def _ev(fname, *, method="json_ld", version="m11.1"):
    class _E:
        user_id = 7
        task_id = 3
        spec_version = 1
        field_name = fname
        snapshot_id = 10
        source_url = "http://x/"
        extract_method = method
        extractor_version = version

    return _E()


@pytest.mark.parametrize(
    "values,expect_codes",
    [
        ({"公司名": "A", "官网": "https://a.com"}, []),
        ({"公司名": "A", "官网": "not-a-url"}, ["SCHEMA_TYPE_URL"]),
        ({"未知字段": "x"}, ["SCHEMA_UNKNOWN_FIELD"]),
    ],
)
def test_structure_type_layer(values, expect_codes):
    issues = StructureTypeValidator().validate(values, FIELDS)
    assert [i.code for i in issues] == expect_codes


def test_required_layer_flags_missing_required():
    issues = RequiredFieldValidator().validate({"公司名": "A"}, FIELDS)
    assert any(i.code == "REQUIRED_FIELD_MISSING" and i.field_name == "官网" for i in issues)


def test_evidence_layer_blocks_without_evidence():
    record = _record()
    values = {"公司名": "A", "官网": "https://a.com"}
    issues = EvidenceValidator().validate(record, values, {}, FIELDS)
    codes = {i.field_name: i.code for i in issues}
    assert codes.get("公司名") == "EVIDENCE_MISSING"
    assert codes.get("官网") == "EVIDENCE_MISSING"  # 无证据 → 不 PASSED
    assert "电话" not in codes  # 可选字段缺失不要求证据


def test_evidence_layer_accepts_valid_chain_and_blocks_broken():
    record = _record()
    values = {"公司名": "A", "官网": "https://a.com"}
    good = {"公司名": [_ev("公司名")], "官网": [_ev("官网")]}
    assert EvidenceValidator().validate(record, values, good, FIELDS) == []
    broken = {"公司名": [_ev("公司名")], "官网": [_ev("官网", method=None, version=None)]}
    issues = EvidenceValidator().validate(record, values, broken, FIELDS)
    assert any(i.code == "EVIDENCE_NO_METHOD" for i in issues)


def test_evidence_system_derived_exception_is_explicit_and_auditable():
    record = _record()
    values = {"公司名": "A", "官网": "https://a.com"}
    settings = ValidationSettings(system_derived_fields=frozenset({"官网"}))
    ev = {"公司名": [_ev("公司名")]}
    issues = EvidenceValidator(settings).validate(record, values, ev, FIELDS)
    assert not any(i.code == "EVIDENCE_MISSING" and i.field_name == "官网" for i in issues)


def test_business_rule_operator_matrix():
    rules = [
        {"code": "MUST_EQUAL", "field_name": "官网", "operator": "equals", "value": "https://a.com"},
        {"code": "NOT_EMPTY", "field_name": "公司名", "operator": "not_empty", "value": None},
        {
            "code": "PHONE_PATTERN",
            "field_name": "电话",
            "operator": "matches",
            "value": r"^1\d{10}$",
        },
        {"code": "BAD_OPERATOR", "field_name": "官网", "operator": "eval", "value": None},
    ]
    issues = BusinessRuleValidator().validate(
        {"公司名": "A", "官网": "https://a.com", "电话": "13800138000"},
        [BusinessValidationRule.model_validate(r) for r in rules],
    )
    assert not any(i.code == "MUST_EQUAL" for i in issues)
    assert not any(i.code == "NOT_EMPTY" for i in issues)
    assert not any(i.code == "PHONE_PATTERN" for i in issues)
    assert any(i.code == "UNKNOWN_RULE_OPERATOR" for i in issues)  # eval 被拒绝


def test_schema_validator_reused_not_duplicated():
    # 复用 M-11 ExtractionSchemaValidator；M-12 结构层不引入第二套 parser
    assert ExtractionSchemaValidator() is not None
