"""M-13 ReviewService：approve/reject/edit 单条审核（D-042）。"""

from __future__ import annotations

import pytest
from app.domain.models import Record
from app.review.contracts import FieldEdit, RecordReviewCommand, ReviewAction
from app.review.repository import ReviewRepository
from app.review.service import ReviewConflictError, ReviewService


def _rec(
    db,
    user_id: int,
    task_id: int,
    partition: str = "needs_review",
    review_type: str = "missing_required",
    payload: dict | None = None,
) -> Record:
    row = Record(
        user_id=user_id,
        task_id=task_id,
        spec_version=1,
        partition=partition,
        review_type=review_type,
        payload=payload or {"标题": "旧值", "文号": "沪府令1号"},
    )
    db.add(row)
    db.flush()
    return row


def test_approve_moves_to_passed_and_audits(db, user_a, task_a) -> None:
    rec = _rec(db, user_a.id, task_a.id)
    svc = ReviewService(db)
    view = svc.execute(
        user_id=user_a.id,
        record_id=rec.id,
        cmd=RecordReviewCommand(
            action=ReviewAction.APPROVE, expected_data_version=rec.data_version
        ),
    )
    assert view.partition == "passed"
    assert view.allowed_actions == []
    # 审计：action_type=approve，记录原 review_type
    from app.domain.models import RecordReviewAction
    from sqlalchemy import select

    act = db.scalar(select(RecordReviewAction).where(RecordReviewAction.record_id == rec.id))
    assert act.action_type == "approve"
    assert act.review_type == "missing_required"


def test_reject_moves_to_rejected(db, user_a, task_a) -> None:
    rec = _rec(db, user_a.id, task_a.id)
    svc = ReviewService(db)
    view = svc.execute(
        user_id=user_a.id,
        record_id=rec.id,
        cmd=RecordReviewCommand(
            action=ReviewAction.REJECT, expected_data_version=rec.data_version, reason="内容不相关"
        ),
    )
    assert view.partition == "rejected"
    assert view.allowed_actions == []


def test_edit_preserves_original_evidence(db, user_a, task_a) -> None:
    rec = _rec(db, user_a.id, task_a.id, payload={"标题": "旧值"})
    svc = ReviewService(db)
    view = svc.execute(
        user_id=user_a.id,
        record_id=rec.id,
        cmd=RecordReviewCommand(
            action=ReviewAction.EDIT,
            expected_data_version=rec.data_version,
            edits=[FieldEdit(field_name="标题", final_value="新值")],
        ),
    )
    assert view.fields["标题"] == "新值"
    assert view.partition == "needs_review"  # edit 不改分区
    ovs = ReviewRepository(db).list_overrides(user_id=user_a.id, record_id=rec.id)
    assert ovs[0].original_value == "旧值"
    assert ovs[0].final_value == "新值"
    assert ovs[0].value_source == "USER_OVERRIDE"
    assert ovs[0].modified_by == user_a.id


def test_stale_version_rejected(db, user_a, task_a) -> None:
    rec = _rec(db, user_a.id, task_a.id)
    svc = ReviewService(db)
    with pytest.raises(ReviewConflictError):
        svc.execute(
            user_id=user_a.id,
            record_id=rec.id,
            cmd=RecordReviewCommand(
                action=ReviewAction.APPROVE, expected_data_version=rec.data_version + 99
            ),
        )


def test_action_not_allowed_for_passed(db, user_a, task_a) -> None:
    rec = _rec(db, user_a.id, task_a.id, partition="passed")
    svc = ReviewService(db)
    with pytest.raises(ReviewConflictError):
        svc.execute(
            user_id=user_a.id,
            record_id=rec.id,
            cmd=RecordReviewCommand(
                action=ReviewAction.REJECT, expected_data_version=rec.data_version
            ),
        )
