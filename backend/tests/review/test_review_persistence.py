"""M-13 persistence：models/migration 0011 表 + overrides + review audit + owner 隔离。"""

from __future__ import annotations

import pytest
from app.auth.errors import NotFoundError
from app.domain.models import Record, RecordReviewAction
from app.review.contracts import RecordListParams
from app.review.repository import ReviewRepository
from sqlalchemy import select
from sqlalchemy.inspection import inspect


def _make_record(
    db,
    user_id: int,
    task_id: int,
    partition: str = "needs_review",
    payload: dict | None = None,
) -> Record:
    row = Record(
        user_id=user_id,
        task_id=task_id,
        spec_version=1,
        partition=partition,
        review_type="missing_required" if partition == "needs_review" else None,
        payload=payload
        or {
            "标题": "测试记录",
            "文号": "沪府令1号",
            "source_type": "official_site",
            "extract_method": "llm",
        },
    )
    db.add(row)
    db.flush()
    return row


def test_migration_0011_tables_and_column_exist(tmp_path) -> None:
    """create_all 依据 SQLAlchemy 模型建表；migration 0011 与模型一致由 alembic 单独验证。"""
    from app.infra.db import Base
    from sqlalchemy import create_engine

    engine = create_engine(
        f"sqlite:///{tmp_path / 'm.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "record_field_overrides" in tables
    assert "record_review_actions" in tables
    cols = {c["name"] for c in insp.get_columns("records")}
    assert "data_version" in cols
    engine.dispose()


def test_create_override_preserves_original_and_final(db, user_a, task_a) -> None:
    rec = _make_record(db, user_a.id, task_a.id, payload={"标题": "旧值"})
    repo = ReviewRepository(db)
    repo.create_override(
        user_id=user_a.id,
        task_id=task_a.id,
        record_id=rec.id,
        field_name="标题",
        original_value="旧值",
        final_value="新值",
        modified_by=user_a.id,
    )
    db.flush()
    overrides = repo.list_overrides(user_id=user_a.id, record_id=rec.id)
    assert len(overrides) == 1
    assert overrides[0].original_value == "旧值"
    assert overrides[0].final_value == "新值"
    assert overrides[0].value_source == "USER_OVERRIDE"
    assert overrides[0].modified_by == user_a.id


def test_create_review_action_audit_fields(db, user_a, task_a) -> None:
    rec = _make_record(db, user_a.id, task_a.id)
    repo = ReviewRepository(db)
    repo.create_review_action(
        user_id=user_a.id,
        task_id=task_a.id,
        record_id=rec.id,
        action_type="approve",
        review_type=None,
        review_reason=None,
        batch_operation_id=None,
        reason="人工确认",
        reviewed_by=user_a.id,
    )
    db.flush()
    row = db.scalar(select(RecordReviewAction).where(RecordReviewAction.record_id == rec.id))
    assert row.action_type == "approve"
    assert row.reviewed_by == user_a.id
    assert row.reviewed_at is not None


def test_query_records_partition_and_and_filter(db, user_a, task_a) -> None:
    _make_record(
        db, user_a.id, task_a.id, partition="passed", payload={"标题": "A", "文号": "沪府令1号"}
    )
    _make_record(db, user_a.id, task_a.id, partition="needs_review", payload={"标题": "B"})
    db.flush()
    repo = ReviewRepository(db)
    total, rows = repo.query_records(
        user_id=user_a.id, task_id=task_a.id, params=RecordListParams(partition="passed")
    )
    assert total == 1 and rows[0].payload["标题"] == "A"
    total, rows = repo.query_records(
        user_id=user_a.id,
        task_id=task_a.id,
        params=RecordListParams(field="文号", value="沪府令1号"),
    )
    assert total == 1


def test_query_records_q_search_and_pagination(db, user_a, task_a) -> None:
    for i in range(5):
        _make_record(
            db,
            user_a.id,
            task_a.id,
            partition="needs_review",
            payload={"标题": f"人工智能政策{i}", "文号": f"沪府{i}号"},
        )
    db.flush()
    repo = ReviewRepository(db)
    total, rows = repo.query_records(
        user_id=user_a.id,
        task_id=task_a.id,
        params=RecordListParams(q="人工智能", page=1, page_size=2),
    )
    assert total == 5
    assert len(rows) == 2


def test_owner_isolation_cross_user(db, user_a, user_b, task_a) -> None:
    rec = _make_record(db, user_a.id, task_a.id)
    db.flush()
    repo = ReviewRepository(db)
    with pytest.raises(NotFoundError):
        repo.get_record_owned(user_id=user_b.id, record_id=rec.id)
    # 覆写查询也隔离
    assert repo.list_overrides(user_id=user_b.id, record_id=rec.id) == []
