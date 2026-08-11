"""字段级 deterministic normalization（M-11 边界：不做业务去重/冲突裁决/质量分区）。

只做 CollectionSpec 明确规则的字段级 canonicalization（十七）。transform 全部注册、
可测试，禁止 eval/任意代码。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

from app.domain.spec import FieldType

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_YES = {"true", "yes", "1", "y", "是", "有"}
_NO = {"false", "no", "0", "n", "否", "无"}
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).strip().split())


def normalize_url(value: str) -> str | None:
    """URL canonical form：scheme/host 小写，保留 path/query（确定性的 canonical 形式）。"""
    text = normalize_text(value)
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()).geturl()


def normalize_email(value: str) -> str | None:
    text = normalize_text(value).lower()
    return text if _EMAIL_RE.match(text) else None


def normalize_number(value: str) -> str | None:
    text = normalize_text(value).replace(",", "").replace("，", "")
    try:
        return str(float(text))
    except ValueError:
        return None


def normalize_phone(value: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", value)
    return digits if digits else None


def normalize_boolean(value: str) -> str | None:
    text = normalize_text(value).lower()
    if text in _YES:
        return "true"
    if text in _NO:
        return "false"
    return None


def normalize_date(value: str) -> str | None:
    text = normalize_text(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_value(value: str, field_type: FieldType) -> str | None:
    """按 CollectionSpec 字段类型做 canonicalization；返回 None 表示值不合法。"""
    text = normalize_text(value)
    if not text:
        return None
    if field_type == FieldType.URL:
        return normalize_url(value)
    if field_type == FieldType.EMAIL:
        return normalize_email(value)
    if field_type == FieldType.NUMBER:
        return normalize_number(value)
    if field_type == FieldType.PHONE:
        return normalize_phone(value)
    if field_type == FieldType.BOOLEAN:
        return normalize_boolean(value)
    if field_type == FieldType.DATE:
        return normalize_date(value)
    return text
