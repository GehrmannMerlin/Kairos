"""M-15 ArtifactService：幂等 CSV 导出 + owner-safe 下载（D-016/D-060/D-072）。"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.artifacts.contracts import (
    ArtifactRef,
    ArtifactView,
    ExportRequest,
    ExportScope,
    ExportType,
)
from app.artifacts.csv_builder import (
    build_csv_bytes,
    final_field_dict,
    schema_columns_for_spec,
)
from app.artifacts.repository import ArtifactRepository
from app.domain.idempotency import stable_fingerprint
from app.domain.models import Record, RecordFieldOverride
from app.domain.repository import SpecVersionRepository, TaskRepository
from app.review.contracts import RecordListParams
from app.review.repository import ReviewRepository

_EXPORT_PARTITION = {
    ExportType.FORMAL: "passed",
    ExportType.REVIEW: "needs_review",
    ExportType.AUDIT: None,  # 三分区
}


def compute_dataset_version(db, *, user_id: int, task_id: int) -> str:
    """数据状态指纹：任何 record 变更（审核/覆写/reprocess）都会变化。

    相同数据 → 相同指纹（导出复用）；任何 final value 变化 → 新指纹（新 Artifact）。
    """
    overrides: dict[int, list[RecordFieldOverride]] = {}
    for o in db.scalars(
        select(RecordFieldOverride).where(
            RecordFieldOverride.user_id == user_id, RecordFieldOverride.task_id == task_id
        )
    ):
        overrides.setdefault(o.record_id, []).append(o)
    records = list(
        db.scalars(
            select(Record)
            .where(Record.user_id == user_id, Record.task_id == task_id)
            .order_by(Record.id.asc())
        )
    )
    entries = [
        (
            r.id,
            r.partition,
            r.review_type,
            r.review_reason,
            r.data_version,
            sorted(final_field_dict(r, overrides).items()),
        )
        for r in records
    ]
    return "ds-" + stable_fingerprint(entries)


def canonical_filter_snapshot(request: ExportRequest) -> dict:
    f: dict = request.filter.model_dump(exclude_none=True)
    forced = _EXPORT_PARTITION[request.export_type]
    snap: dict = {"scope": request.scope.value}
    if forced is not None:
        snap["partition"] = forced
    snap.update({k: v for k, v in f.items() if v not in (None, "")})
    return snap


class ArtifactService:
    def __init__(self, db, storage) -> None:
        self._db = db
        self._storage = storage
        self._repo = ArtifactRepository(db)

    async def export(self, *, user_id: int, task_id: int, request: ExportRequest) -> ArtifactRef:
        TaskRepository(self._db).get_owned(user_id, task_id)
        spec = SpecVersionRepository(self._db).latest_version(user_id, task_id)
        schema_version = f"spec-v{spec.version}/{spec.schema_version}" if spec else "no-spec"
        columns = schema_columns_for_spec(spec.payload if spec else None)

        ds_version = compute_dataset_version(self._db, user_id=user_id, task_id=task_id)
        snapshot = canonical_filter_snapshot(request)
        request_fp = stable_fingerprint(
            ds_version, snapshot, request.export_type.value, schema_version
        )

        existing = self._repo.find_ready(
            user_id=user_id,
            task_id=task_id,
            dataset_version=ds_version,
            export_type=request.export_type.value,
            request_fingerprint=request_fp,
        )
        if existing is not None and existing.content_hash:
            return ArtifactRef(
                artifact_id=existing.id,
                content_hash=existing.content_hash,
                download_url=f"/tasks/{task_id}/artifacts/{existing.id}/download",
                row_count=existing.row_count,
            )

        # 生成 rows（AUDIT 不强制 partition；FORMAL/REVIEW 强制对应分区）
        forced = _EXPORT_PARTITION[request.export_type]
        rows = self._rows_for_export(user_id, task_id, request, forced)
        include_status = request.export_type is ExportType.AUDIT
        data = build_csv_bytes(rows, columns, include_status_fields=include_status)
        content_hash = hashlib.sha256(data).hexdigest()
        key = f"artifacts/u{user_id}/csv/{content_hash}.csv"
        if not await self._storage.exists(key):
            await self._storage.put(key, data, content_type="text/csv; charset=utf-8")

        filename = self._safe_filename(
            TaskRepository(self._db).get_owned(user_id, task_id).title,
            request.export_type.value,
            ds_version,
        )
        try:
            artifact = self._repo.create(
                user_id=user_id,
                task_id=task_id,
                artifact_type="csv",
                dataset_version=ds_version,
                export_type=request.export_type.value,
                filter_snapshot=snapshot,
                request_fingerprint=request_fp,
                schema_version=schema_version,
                content_hash=content_hash,
                storage_ref=key,
                row_count=len(rows),
                size_bytes=len(data),
                filename=filename,
            )
        except IntegrityError:
            # M-16 并发幂等：相同导出并发都越过 find_ready → 部分唯一索引兜底，
            # 回滚后复用已提交的获胜 Artifact（不重复生成 Blob/不重复建行）。
            self._db.rollback()
            existing = self._repo.find_ready(
                user_id=user_id,
                task_id=task_id,
                dataset_version=ds_version,
                export_type=request.export_type.value,
                request_fingerprint=request_fp,
            )
            if existing is not None and existing.content_hash:
                return ArtifactRef(
                    artifact_id=existing.id,
                    content_hash=existing.content_hash,
                    download_url=f"/tasks/{task_id}/artifacts/{existing.id}/download",
                    row_count=existing.row_count,
                )
            raise
        return ArtifactRef(
            artifact_id=artifact.id,
            content_hash=content_hash,
            download_url=f"/tasks/{task_id}/artifacts/{artifact.id}/download",
            row_count=len(rows),
        )

    def _rows_for_export(self, user_id, task_id, request, forced_partition):
        repo = ReviewRepository(self._db)
        if request.scope is ExportScope.ALL:
            base = RecordListParams(partition=forced_partition)
        else:
            base = RecordListParams(
                q=request.filter.q,
                field=request.filter.field,
                value=request.filter.value,
                source_type=request.filter.source_type,
                extract_method=request.filter.extract_method,
                min_confidence=request.filter.min_confidence,
                review_type=request.filter.review_type,
            )
            if forced_partition is not None:
                base.partition = forced_partition
        return repo.query_records_all(user_id=user_id, task_id=task_id, params=base)

    async def download(self, *, user_id: int, task_id: int, artifact_id: int):
        artifact = self._repo.get_owned(
            user_id=user_id, task_id=task_id, artifact_id=artifact_id
        )
        if not artifact.storage_ref:
            raise RuntimeError("artifact content missing")
        data = await self._storage.get(artifact.storage_ref)
        return data, artifact.filename or "export.csv"

    def list_for_task(self, *, user_id: int, task_id: int) -> list[ArtifactView]:
        TaskRepository(self._db).get_owned(user_id, task_id)
        return [
            ArtifactView(
                artifact_id=a.id,
                export_type=a.export_type or "",
                dataset_version=a.dataset_version or "",
                filter_snapshot=a.filter_snapshot or {},
                schema_version=a.schema_version,
                row_count=a.row_count,
                size_bytes=a.size_bytes,
                content_hash=a.content_hash,
                filename=a.filename or "export.csv",
                status=a.status,
                created_at=a.created_at,
                download_url=f"/tasks/{task_id}/artifacts/{a.id}/download",
            )
            for a in self._repo.list_for_task(user_id=user_id, task_id=task_id)
        ]

    @staticmethod
    def _safe_filename(title: str, export_type: str, dataset_version: str) -> str:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", (title or "task"))[:40].strip("._") or "task"
        return f"{base}_{export_type}_{dataset_version[:16]}.csv"
