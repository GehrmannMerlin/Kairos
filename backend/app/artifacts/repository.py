"""M-15 ArtifactRepository：owner-safe 复用查找 + 创建。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.models import Artifact


class ArtifactRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def find_ready(
        self,
        *,
        user_id: int,
        task_id: int,
        dataset_version: str,
        export_type: str,
        request_fingerprint: str,
    ) -> Artifact | None:
        return self._db.scalar(
            select(Artifact)
            .where(
                Artifact.user_id == user_id,
                Artifact.task_id == task_id,
                Artifact.dataset_version == dataset_version,
                Artifact.export_type == export_type,
                Artifact.request_fingerprint == request_fingerprint,
                Artifact.status == "ready",
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )

    def get_owned(self, *, user_id: int, task_id: int, artifact_id: int) -> Artifact:
        row = self._db.get(Artifact, artifact_id)
        if row is None or row.user_id != user_id or row.task_id != task_id:
            from app.auth.errors import NotFoundError

            raise NotFoundError("资源不存在")
        return row

    def create(
        self,
        *,
        user_id: int,
        task_id: int,
        artifact_type: str,
        dataset_version: str,
        export_type: str,
        filter_snapshot: dict,
        request_fingerprint: str,
        schema_version: str | None,
        content_hash: str,
        storage_ref: str,
        row_count: int,
        size_bytes: int,
        filename: str,
        status: str = "ready",
    ) -> Artifact:
        row = Artifact(
            user_id=user_id,
            task_id=task_id,
            artifact_type=artifact_type,
            dataset_version=dataset_version,
            export_type=export_type,
            filter_snapshot=filter_snapshot,
            request_fingerprint=request_fingerprint,
            schema_version=schema_version,
            content_hash=content_hash,
            storage_ref=storage_ref,
            row_count=row_count,
            size_bytes=size_bytes,
            filename=filename,
            status=status,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def list_for_task(self, *, user_id: int, task_id: int) -> list[Artifact]:
        return list(
            self._db.scalars(
                select(Artifact)
                .where(Artifact.user_id == user_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.desc())
            )
        )
