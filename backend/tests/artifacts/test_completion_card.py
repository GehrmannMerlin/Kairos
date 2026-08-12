"""M-15 Completion Card：NORMAL/PARTIAL 来自 DB facts，无假百分比（D-006/D-043）。"""

from __future__ import annotations

from app.api.routes.completion import assemble_completion_card
from app.domain.models import CompletionDecision


def _decision(
    db,
    user,
    task,
    *,
    status="NORMAL_COMPLETED",
    is_partial=False,
    ctype="directional_scope_complete",
):
    d = CompletionDecision(
        user_id=user.id,
        task_id=task.id,
        run_id=None,
        spec_version=1,
        plan_version=1,
        status=status,
        is_partial=is_partial,
        completion_type=ctype,
        qualified_record_count=3,
        scope_completion_metadata={"eligible_urls": 5, "terminal_urls": 5},
    )
    db.add(d)
    db.flush()
    return d


def test_completion_normal(db, user_a, task_a) -> None:
    _decision(db, user_a, task_a)
    view = assemble_completion_card(db, user_id=user_a.id, task_id=task_a.id)
    assert view.status == "NORMAL_COMPLETED"
    assert view.is_partial is False
    assert view.completion_id is not None
    assert view.qualified_record_count == 3
    assert view.can_export_formal is False  # 无 passed record


def test_completion_partial_no_fake_percent(db, user_a, task_a) -> None:
    _decision(
        db, user_a, task_a, status="PARTIALLY_COMPLETED", is_partial=True, ctype="runtime_limit"
    )
    view = assemble_completion_card(db, user_id=user_a.id, task_id=task_a.id)
    assert view.is_partial is True
    assert view.completion_type == "runtime_limit"
    # 契约上不存在任何百分比字段（无 fake %）
    assert "percent" not in view.model_dump()


def test_completion_with_counts(db, user_a, task_a) -> None:
    from app.domain.models import Record

    _decision(db, user_a, task_a)
    db.add_all(
        [
            Record(
                user_id=user_a.id,
                task_id=task_a.id,
                spec_version=1,
                partition="passed",
                payload={"a": "1"},
            ),
            Record(
                user_id=user_a.id,
                task_id=task_a.id,
                spec_version=1,
                partition="needs_review",
                payload={"a": "2"},
            ),
        ]
    )
    db.flush()
    view = assemble_completion_card(db, user_id=user_a.id, task_id=task_a.id)
    assert view.partition_counts["passed"] == 1
    assert view.partition_counts["needs_review"] == 1
    assert view.can_export_formal is True
    assert view.can_export_review is True
