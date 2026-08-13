"""M-15 Artifact Query/Export/Download API（D-060/D-072）。owner-safe，越权 404。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession

from app.artifacts.contracts import ArtifactRef, ArtifactView, ExportRequest
from app.artifacts.service import ArtifactService
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db, storage
from app.infra.object_storage import ObjectStorage

router = APIRouter(prefix="/tasks/{task_id}/artifacts", tags=["artifacts"])


@router.post("/export", response_model=ArtifactRef)
async def export_artifact(
    task_id: int,
    request: ExportRequest,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    object_storage: ObjectStorage = Depends(storage),
) -> ArtifactRef:
    TaskRepository(db).get_owned(user.id, task_id)
    return await ArtifactService(db, object_storage).export(
        user_id=user.id, task_id=task_id, request=request
    )


@router.get("", response_model=list[ArtifactView])
def list_artifacts(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> list[ArtifactView]:
    TaskRepository(db).get_owned(user.id, task_id)
    return ArtifactService(db, None).list_for_task(user_id=user.id, task_id=task_id)


@router.get("/{artifact_id}/download")
async def download_artifact(
    task_id: int,
    artifact_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    object_storage: ObjectStorage = Depends(storage),
) -> StreamingResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    data, filename = await ArtifactService(db, object_storage).download(
        user_id=user.id, task_id=task_id, artifact_id=artifact_id
    )
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
