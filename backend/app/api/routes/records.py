"""Records Query + Review Command API（M-13，D-041/042/060/061/062）。

GET  /tasks/{task_id}/records                 → RecordListResponse（分页/搜索/筛选/排序/计数）
GET  /tasks/{task_id}/records/{record_id}     → RecordDetailView（Drawer，字段+证据+覆写）
POST /tasks/{task_id}/records/{record_id}/review      → RecordReviewResponse
POST /tasks/{task_id}/records/batch-review            → BatchReviewResponse

全部 owner-safe：task/record 越权统一 404（不泄漏存在性）。DTO 契约来自
app.review.contracts，不复制第二套 Record 事实。
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.review.contracts import (
    BatchReviewCommand,
    BatchReviewResponse,
    RecordDetailView,
    RecordListParams,
    RecordListResponse,
    RecordReviewCommand,
    RecordReviewResponse,
)
from app.review.repository import ReviewRepository
from app.review.service import ReviewService
from app.review.views import to_detail, to_view
from app.validation.repository import ValidationRepository

router = APIRouter(prefix="/tasks/{task_id}/records", tags=["records"])


def _get_task(db: DbSession, user_id: int, task_id: int) -> None:
    # owner-safe：无权限/不存在 → 404
    TaskRepository(db).get_owned(user_id, task_id)


@router.get("", response_model=RecordListResponse)
def query_records(
    task_id: int,
    partition: str | None = Query(default=None, pattern="^(passed|needs_review|rejected)$"),
    q: str | None = None,
    field: str | None = None,
    value: str | None = None,
    source_type: str | None = None,
    extract_method: str | None = None,
    min_confidence: float | None = None,
    review_type: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "asc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> RecordListResponse:
    _get_task(db, user.id, task_id)
    params = RecordListParams(
        partition=cast("Literal['passed', 'needs_review', 'rejected'] | None", partition),
        q=q,
        field=field,
        value=value,
        source_type=source_type,
        extract_method=extract_method,
        min_confidence=min_confidence,
        review_type=review_type,
        sort_by=sort_by,
        sort_order=cast(Literal["asc", "desc"], sort_order),
        page=page,
        page_size=page_size,
    )
    repo = ReviewRepository(db)
    total, rows = repo.query_records(user_id=user.id, task_id=task_id, params=params)
    counts = repo.count_by_partition(user_id=user.id, task_id=task_id)
    snap = ValidationRepository(db).latest_snapshot(user_id=user.id, task_id=task_id)
    items = [
        to_view(
            r, repo.list_overrides(user_id=user.id, record_id=r.id), repo.url_for_record(record=r)
        )
        for r in rows
    ]
    return RecordListResponse(
        task_id=task_id,
        partition_counts=counts,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        dataset_version=snap.dataset_version if snap else None,
    )


@router.get("/{record_id}", response_model=RecordDetailView)
def get_record_detail(
    task_id: int,
    record_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> RecordDetailView:
    _get_task(db, user.id, task_id)
    repo = ReviewRepository(db)
    record = repo.get_record_owned(user_id=user.id, record_id=record_id)
    overrides = repo.list_overrides(user_id=user.id, record_id=record_id)
    evidence = repo.evidence_for_record(user_id=user.id, record_id=record_id)
    return to_detail(record, overrides, evidence, repo.url_for_record(record=record))


@router.post("/{record_id}/review", response_model=RecordReviewResponse)
def review_record(
    task_id: int,
    record_id: int,
    cmd: RecordReviewCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> RecordReviewResponse:
    _get_task(db, user.id, task_id)
    view = ReviewService(db).execute(user_id=user.id, record_id=record_id, cmd=cmd)
    return RecordReviewResponse(record=view)


@router.post("/batch-review", response_model=BatchReviewResponse)
def batch_review(
    task_id: int,
    cmd: BatchReviewCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> BatchReviewResponse:
    _get_task(db, user.id, task_id)
    return ReviewService(db).batch(user_id=user.id, task_id=task_id, cmd=cmd)
