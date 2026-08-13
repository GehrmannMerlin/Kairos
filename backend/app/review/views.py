"""M-13 Record → DTO 视图组装。fields 最终值 = payload 叠加人工覆写（D-042）。"""

from __future__ import annotations

from app.domain.models import FieldEvidence, Record, RecordFieldOverride
from app.review.contracts import RecordDetailView, RecordFieldDetail, RecordView
from app.review.policy import ReviewPolicy


def _record_field_dict(record: Record) -> dict:
    """Record 最终字段 dict。

    真实提取记录（M-11）把字段值嵌套在 payload["values"]；fixture/旧数据平铺在
    payload。统一展平为字段 dict，使 to_view/to_detail 对真实记录也能暴露
    字段值 + FieldEvidence（snapshot_id 等证据引用）。
    """
    payload = record.payload or {}
    values = payload.get("values")
    if isinstance(values, dict):
        return dict(values)
    return dict(payload)


def _apply_overrides(payload: dict, overrides: list[RecordFieldOverride]) -> dict:
    final = dict(payload)
    for o in overrides:
        final[o.field_name] = o.final_value
    return final


def to_view(
    record: Record, overrides: list[RecordFieldOverride], source_url: str | None
) -> RecordView:
    return RecordView(
        record_id=record.id,
        task_id=record.task_id,
        partition=record.partition,
        review_type=record.review_type,
        review_reason=record.review_reason,
        data_version=record.data_version,
        fields=_apply_overrides(_record_field_dict(record), overrides),
        source_url=source_url,
        created_at=record.created_at,
        updated_at=record.updated_at,
        allowed_actions=ReviewPolicy.allowed_actions(record=record),
    )


def _scalar(value: object) -> str:
    return value if isinstance(value, str) else str(value or "")


def to_detail(
    record: Record,
    overrides: list[RecordFieldOverride],
    evidence: list[FieldEvidence],
    source_url: str | None,
) -> RecordDetailView:
    override_by_field = {o.field_name: o for o in overrides}
    ev_by_field: dict[str, FieldEvidence] = {}
    for ev in evidence:
        if ev.field_name and ev.field_name not in ev_by_field:
            ev_by_field[ev.field_name] = ev
    fields: list[RecordFieldDetail] = []
    seen: set[str] = set()
    for key, value in _record_field_dict(record).items():
        seen.add(key)
        ov = override_by_field.get(key)
        evidence_detail = ev_by_field.get(key)
        fields.append(
            RecordFieldDetail(
                field_name=key,
                value=ov.final_value if ov else _scalar(value),
                original_value=ov.original_value if ov else None,
                value_source=ov.value_source if ov else "EXTRACTED",
                extract_method=evidence_detail.extract_method if evidence_detail else None,
                extractor_version=evidence_detail.extractor_version if evidence_detail else None,
                confidence=evidence_detail.confidence if evidence_detail else None,
                source_url=evidence_detail.source_url if evidence_detail else source_url,
                snapshot_id=evidence_detail.snapshot_id if evidence_detail else None,
            )
        )
    for key, ov in override_by_field.items():
        if key not in seen:
            fields.append(
                RecordFieldDetail(
                    field_name=key,
                    value=ov.final_value,
                    original_value=ov.original_value,
                    value_source=ov.value_source,
                    extract_method=None,
                    extractor_version=None,
                    confidence=None,
                    source_url=None,
                    snapshot_id=None,
                )
            )
    return RecordDetailView(
        record_id=record.id,
        task_id=record.task_id,
        partition=record.partition,
        review_type=record.review_type,
        review_reason=record.review_reason,
        data_version=record.data_version,
        allowed_actions=ReviewPolicy.allowed_actions(record=record),
        fields=fields,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
