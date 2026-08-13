"""Deterministic CSV bytes（D-005/D-021）。

- UTF-8 with BOM + CRLF（Excel 兼容）；csv 模块 QUOTE_MINIMAL。
- 业务列来自冻结 CollectionSpec field schema（deterministic order）。
- 行序固定 record.id ASC（不受 UI sort/分页影响，由调用方排序）。
- 正式/待复核 CSV 不输出状态列；AUDIT 输出 partition/review_type/review_reason 审计列。
- 使用 final value（USER_OVERRIDE 叠加），绝不改写 original_value/FieldEvidence。
"""

from __future__ import annotations

import csv
import io

from app.domain.models import Record, RecordFieldOverride


def _flatten_values(payload: dict) -> dict:
    """M-11 真实记录字段嵌套在 payload['values']；fixture 平铺在 payload。"""
    values = payload.get("values")
    return dict(values) if isinstance(values, dict) else dict(payload)


def final_field_dict(
    record: Record, override_by_record: dict[int, list[RecordFieldOverride]]
) -> dict:
    """Record 最终字段 dict = 展平 payload 叠加人工覆写（与 app.review.views 一致）。"""
    final = _flatten_values(record.payload or {})
    for ov in override_by_record.get(record.id, []):
        final[ov.field_name] = ov.final_value
    return final


def schema_columns_for_spec(spec_payload: dict | None) -> list[str]:
    """冻结 CollectionSpec 的业务列，按 spec.fields 声明顺序。"""
    fields = (spec_payload or {}).get("fields") or []
    out: list[str] = []
    for f in fields:
        if isinstance(f, dict):
            name = f.get("name")
            if isinstance(name, str) and name:
                out.append(name)
    return out


STATUS_COLUMNS = ["partition", "review_type", "review_reason"]


def build_csv_bytes(
    records: list[Record],
    columns: list[str],
    *,
    include_status_fields: bool,
) -> bytes:
    """确定性 CSV bytes；records 需已按 record.id ASC 排序。"""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    header = columns + (STATUS_COLUMNS if include_status_fields else [])
    writer.writerow(header)
    for r in records:
        fields = _flatten_values(r.payload or {})
        row: list[str] = []
        for col in columns:
            v = fields.get(col)
            row.append("" if v is None else str(v))
        if include_status_fields:
            row += [r.partition, r.review_type or "", r.review_reason or ""]
        writer.writerow(row)
    raw = buf.getvalue()
    return b"\xef\xbb\xbf" + raw.encode("utf-8")  # UTF-8 BOM
