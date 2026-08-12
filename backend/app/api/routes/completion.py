"""M-15 Completion Card Query API（D-006/D-043/D-044）。

GET /tasks/{task_id}/completion → CompletionCardView。全部来自 DB facts：
CompletionDecision（最新）+ 分区计数 + URLResource 处理事实；不调用 LLM。
owner-safe：任务越权 → 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.artifacts.contracts import CompletionCardView
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.quality.repository import QualityRepository
from app.validation.repository import ValidationRepository

router = APIRouter(prefix="/tasks/{task_id}/completion", tags=["completion"])


def assemble_completion_card(db, *, user_id: int, task_id: int) -> CompletionCardView:
    decision = ValidationRepository(db).latest_completion(user_id=user_id, task_id=task_id)
    counts = QualityRepository(db).count_by_partition(user_id=user_id, task_id=task_id)
    urls = QualityRepository(db).url_resources(user_id=user_id, task_id=task_id)
    terminal = sum(1 for u in urls if u.status in ("FETCHED", "HANDED_OFF"))
    passed = int(counts.get("passed", 0))
    review = int(counts.get("needs_review", 0))
    rejected = int(counts.get("rejected", 0))
    return CompletionCardView(
        task_id=task_id,
        completion_id=decision.id if decision else None,
        status=decision.status if decision else "PARTIALLY_COMPLETED",
        reason=decision.reason if decision else "未找到完成判定记录",
        completion_type=decision.completion_type if decision else None,
        is_partial=bool(decision.is_partial) if decision else True,
        qualified_record_count=int(decision.qualified_record_count) if decision else 0,
        partition_counts={"passed": passed, "needs_review": review, "rejected": rejected},
        url_processed=terminal,
        runtime_limit_reason=decision.runtime_limit_reason if decision else None,
        scope_completion_metadata=(decision.scope_completion_metadata or {}) if decision else {},
        can_view_data=True,
        can_view_quality=True,
        can_export_formal=passed > 0,
        can_export_review=review > 0,
    )


@router.get("", response_model=CompletionCardView)
def get_completion(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> CompletionCardView:
    TaskRepository(db).get_owned(user.id, task_id)
    return assemble_completion_card(db, user_id=user.id, task_id=task_id)
