"""M-12 typed BusinessValidationRule 注册表（D-014 业务规则层）。

规则必须 deterministic。禁止任意 Python 代码存 DB 后 eval；只允许代码注册的
操作符（equals/not_empty/in_enum/range_min/range_max/matches/co_present）。
规则来源：CollectionSpec 约束或代码注册 typed safe config。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.validation.contracts import ValidationIssue

_STRICT = ConfigDict(extra="forbid")


class BusinessValidationRule(BaseModel):
    model_config = _STRICT

    code: str
    field_name: str
    operator: str  # 见 RULE_OPERATORS
    value: Any | None = None
    description: str = ""
    severity: str = "error"


def _equals(field_value: Any, value: Any) -> bool:
    return str(field_value).strip() == str(value).strip()


def _not_empty(field_value: Any, value: Any) -> bool:
    return field_value is not None and str(field_value).strip() != ""


def _in_enum(field_value: Any, value: Any) -> bool:
    return str(field_value).strip() in {str(v).strip() for v in (value or [])}


def _range_min(field_value: Any, value: Any) -> bool:
    try:
        return float(field_value) >= float(value)
    except (TypeError, ValueError):
        return False


def _range_max(field_value: Any, value: Any) -> bool:
    try:
        return float(field_value) <= float(value)
    except (TypeError, ValueError):
        return False


def _matches(field_value: Any, value: Any) -> bool:
    try:
        return re.search(str(value), str(field_value)) is not None
    except re.error:
        return False


def _co_present(field_value: Any, value: Any) -> bool:
    # 主字段非空时 companion（value=list of field names）也须非空；
    # 实际 companion 校验由 BusinessRuleValidator 按 record_values 组合完成。
    return True


RULE_OPERATORS: dict[str, Any] = {
    "equals": _equals,
    "not_empty": _not_empty,
    "in_enum": _in_enum,
    "range_min": _range_min,
    "range_max": _range_max,
    "matches": _matches,
    "co_present": _co_present,
}


class BusinessRuleValidator:
    def validate(
        self, record_values: dict, rules: list[BusinessValidationRule]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for rule in rules:
            op = RULE_OPERATORS.get(rule.operator)
            if op is None:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_RULE_OPERATOR",
                        field_name=rule.field_name,
                        detail=f"未知操作符 {rule.operator}",
                        severity=rule.severity,
                    )
                )
                continue
            value = record_values.get(rule.field_name)
            ok = op(value, rule.value)
            if rule.operator == "co_present" and value not in (None, ""):
                companions = rule.value or []
                ok = all(record_values.get(c) not in (None, "") for c in companions)
            if not ok:
                issues.append(
                    ValidationIssue(
                        code=rule.code,
                        field_name=rule.field_name,
                        detail=rule.description or rule.code,
                        severity=rule.severity,
                    )
                )
        return issues


__all__ = ["BusinessValidationRule", "BusinessRuleValidator", "RULE_OPERATORS"]
