"""M-15 CSV 生成器确定性 + USER_OVERRIDE final value（D-005/D-021/D-042）。"""

from __future__ import annotations

from app.artifacts.csv_builder import (
    build_csv_bytes,
    final_field_dict,
    schema_columns_for_spec,
)
from app.domain.models import Record, RecordFieldOverride


def test_schema_columns_deterministic_from_spec() -> None:
    spec = {
        "fields": [
            {"name": "标题", "type": "text"},
            {"name": "文号", "type": "text"},
        ]
    }
    assert schema_columns_for_spec(spec) == ["标题", "文号"]


def test_final_field_dict_uses_override() -> None:
    r = Record(
        id=1,
        user_id=1,
        task_id=1,
        spec_version=1,
        partition="passed",
        payload={"values": {"标题": "原始", "文号": "X1"}},
    )
    ov = RecordFieldOverride(
        user_id=1,
        task_id=1,
        record_id=1,
        field_name="标题",
        final_value="人工值",
        value_source="USER_OVERRIDE",
        modified_by=1,
    )
    out = final_field_dict(r, {r.id: [ov]})
    assert out["标题"] == "人工值"
    assert out["文号"] == "X1"


def test_csv_bytes_stable_utf8_bom() -> None:
    records = [
        Record(
            user_id=1,
            task_id=1,
            spec_version=1,
            partition="passed",
            payload={"标题": "a", "文号": "b"},
        )
    ]
    data = build_csv_bytes(records, ["标题", "文号"], include_status_fields=False)
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    assert data == build_csv_bytes(records, ["标题", "文号"], include_status_fields=False)
    text = data.decode("utf-8-sig")
    assert text.startswith("标题,文号\r\n")
    assert "a,b\r\n" in text


def test_csv_audit_includes_status_fields() -> None:
    records = [
        Record(
            user_id=1,
            task_id=1,
            spec_version=1,
            partition="needs_review",
            review_type="missing_required",
            review_reason="missing_required",
            payload={"标题": "a"},
        )
    ]
    data = build_csv_bytes(records, ["标题"], include_status_fields=True)
    text = data.decode("utf-8-sig")
    assert text.startswith("标题,partition,review_type,review_reason\r\n")
    assert "a,needs_review,missing_required,missing_required\r\n" in text
