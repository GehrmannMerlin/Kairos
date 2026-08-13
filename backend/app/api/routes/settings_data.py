"""M-15 设置 → 存储与数据（D-052/D-072）。只读摘要 + retention dry-run 预览；不暴露 MinIO 内部。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.artifacts.retention import CleanupResult, RetentionService
from app.auth.deps import require_user
from app.auth.models import User
from app.config import get_settings
from app.domain.models import Artifact, FieldEvidence, PageSnapshot, Record, Task
from app.infra.deps import get_db, storage
from app.infra.object_storage import ObjectStorage

router = APIRouter(prefix="/settings", tags=["settings-data"])


class StorageSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_count: int
    record_count: int
    evidence_count: int
    artifact_count: int
    snapshot_bytes: int
    artifact_bytes: int
    retention_days: int


@router.get("/storage-summary", response_model=StorageSummaryView)
def storage_summary(
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> StorageSummaryView:
    uid = user.id

    def _count(model):
        return int(
            db.scalar(select(func.count()).select_from(model).where(model.user_id == uid)) or 0
        )

    def _sum_bytes(col, model):
        return int(
            db.scalar(
                select(func.coalesce(func.sum(col), 0)).select_from(model).where(
                    model.user_id == uid
                )
            )
            or 0
        )

    return StorageSummaryView(
        task_count=_count(Task),
        record_count=_count(Record),
        evidence_count=_count(FieldEvidence),
        artifact_count=_count(Artifact),
        snapshot_bytes=_sum_bytes(PageSnapshot.download_bytes, PageSnapshot),
        artifact_bytes=_sum_bytes(Artifact.size_bytes, Artifact),
        retention_days=int(get_settings().retention_heavy_days),
    )


@router.post("/storage/cleanup-preview", response_model=CleanupResult)
async def cleanup_preview(
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    object_storage: ObjectStorage = Depends(storage),
) -> CleanupResult:
    """retention dry-run 预览：只统计，不删除（§54：Staging 默认只跑 dry-run）。"""
    svc = RetentionService(
        db, object_storage, retention_days=int(get_settings().retention_heavy_days)
    )
    return await svc.run(dry_run=True)
