"""M-12 任务级 BusinessUniqueKeyStrategy + deterministic dedupe（D-014 / D-016）。

business_key_fingerprint 只包含被 Spec/策略声明为 key 的 normalized 字段值，绝不包含
timestamp/random UUID/extractor attempt（模块需求 19）。LLM 只允许产出 possible_duplicate
candidate pairs；最终自动 merge 必须达到 deterministic threshold，否则 NEEDS_REVIEW
（模块需求 17/21）。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.idempotency import stable_fingerprint
from app.domain.spec import FieldSpec, FieldType, validate_spec_payload
from app.extraction.normalize import (
    normalize_email,
    normalize_number,
    normalize_phone,
    normalize_url,
)
from app.validation.policies import ValidationSettings

_STRICT = ConfigDict(extra="forbid")


class BusinessKeyPolicy(BaseModel):
    model_config = _STRICT

    key_fields: list[str]


class BusinessUniqueKeyStrategy:
    """从 CollectionSpec + task type + field schema 确定 deterministic business key。

    默认策略：key = 全部必填字段（通用 typed 定义）。企业例子「normalized company
    name + official domain」只是必填字段恰好为这两个的实例，不硬编码为所有任务通用
    （模块需求 18）。
    """

    def resolve(self, spec_payload: dict) -> BusinessKeyPolicy:
        spec = validate_spec_payload(spec_payload)
        key_fields = [f.name for f in spec.fields if f.required]
        return BusinessKeyPolicy(key_fields=key_fields)


def _normalize_for_key(value: Any, field_type: FieldType) -> str:
    text = str(value or "").strip()
    if field_type == FieldType.URL:
        return normalize_url(text) or text.lower()
    if field_type == FieldType.EMAIL:
        return normalize_email(text) or text.lower()
    if field_type == FieldType.PHONE:
        return normalize_phone(text) or "".join(c for c in text if c.isdigit())
    if field_type == FieldType.NUMBER:
        return str(normalize_number(text) or text)
    return text


def compute_business_key(
    record_values: dict, policy: BusinessKeyPolicy, fields: list[FieldSpec]
) -> str | None:
    """normalized key 值组合；任一 key 字段缺失返回 None（无法 exact dedupe）。"""
    field_by_name = {f.name: f for f in fields}
    parts: list[str] = []
    for name in policy.key_fields:
        value = record_values.get(name)
        if value in (None, ""):
            return None
        ftype = field_by_name.get(name, FieldSpec(name=name)).type
        parts.append(_normalize_for_key(value, ftype))
    return " | ".join(parts)


def business_key_fingerprint(*key_parts: str) -> str:
    return stable_fingerprint("bizkey", *key_parts)


_BUSINESS_KEY_COLUMN_CHARS = 500


def bounded_business_key(text: str | None, *, limit: int = _BUSINESS_KEY_COLUMN_CHARS) -> str:
    """Bound the stored display preview to the column width.

    ``business_key_fingerprint`` is the stable identity (and is always computed from the
    full canonical key); the raw ``business_key`` column is only a human-readable preview,
    so it must never overflow ``VARCHAR(500)`` and trigger ``StringDataRightTruncation``.
    Truncation is trailing-only and deterministic; two distinct long keys may share a
    preview, which is harmless because identity is carried by the fingerprint.
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class DedupeEngine:
    """exact dedupe + deterministic fuzzy candidate 生成（模块需求 20-22）。

    exact 相同 fingerprint 自动同组；fuzzy 仅当两 normalized key 相似度 >=
    dedupe_min_similarity 才自动并入（deterministic），否则不进组 → 由流水线判
    NEEDS_REVIEW。绝不删除 Evidence/候选历史。
    """

    def __init__(self, settings: ValidationSettings | None = None) -> None:
        self._settings = settings or ValidationSettings()

    def group(
        self, records: list[Any], policy: BusinessKeyPolicy, fields: list[FieldSpec]
    ) -> tuple[list[dict], list[Any]]:
        """返回 (groups, ungrouped)。

        groups: [{business_key, business_key_fingerprint, record_ids, approximate}]
        """
        exact: dict[str, list[Any]] = {}
        for rec in records:
            values = (rec.payload or {}).get("values") or {}
            key = compute_business_key(values, policy, fields)
            if key is None:
                continue
            exact.setdefault(business_key_fingerprint(key), []).append(rec)

        groups: list[dict] = []
        for fp, recs in exact.items():
            groups.append(
                {
                    "business_key": _key_of(recs[0], policy, fields),
                    "business_key_fingerprint": fp,
                    "record_ids": [r.id for r in recs],
                    "approximate": False,
                }
            )
        self._fuzzy_merge(groups, policy, fields)
        grouped_ids = {rid for g in groups for rid in g["record_ids"]}
        ungrouped = [r for r in records if r.id not in grouped_ids]
        return groups, ungrouped

    def _fuzzy_merge(
        self, groups: list[dict], policy: BusinessKeyPolicy, fields: list[FieldSpec]
    ) -> None:
        if len(groups) < 2:
            return
        i = 0
        while i < len(groups):
            j = i + 1
            while j < len(groups):
                a = groups[i]["business_key"]
                b = groups[j]["business_key"]
                if _similarity(a, b) >= self._settings.dedupe_min_similarity:
                    groups[i]["record_ids"].extend(groups[j]["record_ids"])
                    groups[i]["approximate"] = True
                    groups.pop(j)
                else:
                    j += 1
            i += 1


def _key_of(record: Any, policy: BusinessKeyPolicy, fields: list[FieldSpec]) -> str:
    values = (record.payload or {}).get("values") or {}
    key = compute_business_key(values, policy, fields)
    return key or ""


__all__ = [
    "BusinessKeyPolicy",
    "BusinessUniqueKeyStrategy",
    "DedupeEngine",
    "bounded_business_key",
    "business_key_fingerprint",
    "compute_business_key",
]
