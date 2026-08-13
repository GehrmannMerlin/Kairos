"""M-13 批量审核：语义兼容门禁 + batch_operation_id 审计（D-061）。"""

from __future__ import annotations

from app.domain.models import Record, RecordReviewAction
from app.review.contracts import BatchReviewCommand
from app.review.service import ReviewService
from sqlalchemy import select


def _recs(db, user_id: int, task_id: int, count: int = 2, review_reason: str = "missing_required"):
    rows = []
    for i in range(count):
        r = Record(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            partition="needs_review",
            review_type=review_reason,
            review_reason=review_reason,
            payload={"标题": f"r{i}"},
        )
        db.add(r)
        db.flush()
        rows.append(r)
    return rows


def test_batch_approve_same_reason(db, user_a, task_a) -> None:
    rows = _recs(db, user_a.id, task_a.id)
    svc = ReviewService(db)
    resp = svc.batch(
        user_id=user_a.id,
        task_id=task_a.id,
        cmd=BatchReviewCommand(
            action="approve",
            record_ids=[r.id for r in rows],
            expected_data_versions={r.id: r.data_version for r in rows},
        ),
    )
    assert resp.batch_operation_id
    assert all(item.ok for item in resp.results)
    assert all(item.partition == "passed" for item in resp.results)
    # 审计：每条记录一条 record_review_actions，带 batch_operation_id
    acts = db.scalars(
        select(RecordReviewAction).where(
            RecordReviewAction.batch_operation_id == resp.batch_operation_id
        )
    ).all()
    assert len(acts) == 2
    assert all(a.action_type == "approve" for a in acts)


def test_batch_approve_mixed_reason_rejected(db, user_a, task_a) -> None:
    r1 = Record(
        user_id=user_a.id,
        task_id=task_a.id,
        spec_version=1,
        partition="needs_review",
        review_reason="missing_required",
        payload={"标题": "a"},
    )
    r2 = Record(
        user_id=user_a.id,
        task_id=task_a.id,
        spec_version=1,
        partition="needs_review",
        review_reason="low_evidence_confidence",
        payload={"标题": "b"},
    )
    db.add_all([r1, r2])
    db.flush()
    svc = ReviewService(db)
    resp = svc.batch(
        user_id=user_a.id,
        task_id=task_a.id,
        cmd=BatchReviewCommand(action="approve", record_ids=[r1.id, r2.id]),
    )
    # 整批拒绝，不允许部分通过
    assert all(not item.ok for item in resp.results)
    assert "批量通过" in resp.results[0].error
    assert all(item.partition is None for item in resp.results)


def test_batch_reject_mixed_reason_ok(db, user_a, task_a) -> None:
    r1 = Record(
        user_id=user_a.id,
        task_id=task_a.id,
        spec_version=1,
        partition="needs_review",
        review_reason="missing_required",
        payload={"标题": "a"},
    )
    r2 = Record(
        user_id=user_a.id,
        task_id=task_a.id,
        spec_version=1,
        partition="needs_review",
        review_reason="low_evidence_confidence",
        payload={"标题": "b"},
    )
    db.add_all([r1, r2])
    db.flush()
    svc = ReviewService(db)
    resp = svc.batch(
        user_id=user_a.id,
        task_id=task_a.id,
        cmd=BatchReviewCommand(action="reject", record_ids=[r1.id, r2.id]),
    )
    assert all(item.ok for item in resp.results)
    assert all(item.partition == "rejected" for item in resp.results)
