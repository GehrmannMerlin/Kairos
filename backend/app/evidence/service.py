"""M-14 Evidence read-model service（D-056/D-064）。

- get()：装配 EvidenceView（display_mode 按 D-064 优先级：image→snapshot；有正文
  snippet→text；否则→raw）。只读 DB + 已有快照元数据，绝不发起 HTTP 抓取。
- content()：owner 校验后从 ObjectStorage 读取历史对象字节（never live source）。
  不返回 MinIO key / 内部 storage_ref。
"""

from __future__ import annotations

from typing import Any, Literal

from app.evidence.contracts import (
    EvidenceFieldEvidenceDto,
    EvidenceView,
)
from app.evidence.repository import EvidenceRepository

DisplayMode = Literal["snapshot", "text", "raw"]


def _is_image(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    return mime_type.split(";")[0].strip().lower().startswith("image/")


class EvidenceService:
    def __init__(self, db: Any, storage: Any) -> None:
        self._db = db
        self._repo = EvidenceRepository(db)
        self._storage = storage

    def get(self, *, user_id: int, task_id: int, snapshot_id: int) -> EvidenceView:
        snap = self._repo.snapshot_for_task(
            user_id=user_id, task_id=task_id, snapshot_id=snapshot_id
        )
        field_evidence = self._repo.field_evidence_for_snapshot(
            user_id=user_id, snapshot_id=snapshot_id
        )
        dto_items = [
            EvidenceFieldEvidenceDto(
                record_id=int(ev.record_id),
                field_name=ev.field_name or "",
                value=ev.value,
                raw_snippet=ev.raw_snippet,
                source_locator=ev.source_locator,
                extract_method=ev.extract_method,
                extractor_version=ev.extractor_version,
                confidence=ev.confidence,
            )
            for ev in field_evidence
        ]
        display_mode, summary = self._display_mode(dto_items, snap.mime_type)
        return EvidenceView(
            evidence_id=snap.id,
            task_id=snap.task_id,
            source_url=snap.final_url or "",
            fetched_at=snap.captured_at,
            snapshot_version=snap.snapshot_version,
            tool=snap.tool,
            tool_version=snap.tool_version,
            mime_type=snap.mime_type,
            http_status=snap.http_status,
            content_length=snap.content_length,
            display_mode=display_mode,
            summary=summary,
            field_evidence=dto_items,
            has_content=bool(snap.storage_ref),
            download_url=f"/tasks/{task_id}/evidence/{snapshot_id}/content",
        )

    @staticmethod
    def _display_mode(
        field_evidence: list[EvidenceFieldEvidenceDto], mime_type: str | None
    ) -> tuple[DisplayMode, str | None]:
        if _is_image(mime_type):
            return "snapshot", None
        for ev in field_evidence:
            snippet = (ev.raw_snippet or "").strip()
            if snippet:
                return "text", snippet[:200]
        return "raw", None

    async def content(self, *, user_id: int, task_id: int, snapshot_id: int) -> tuple[bytes, str]:
        snap = self._repo.snapshot_for_task(
            user_id=user_id, task_id=task_id, snapshot_id=snapshot_id
        )
        if not snap.storage_ref:
            from app.auth.errors import NotFoundError

            raise NotFoundError("资源不存在")
        data = await self._storage.get(snap.storage_ref)
        return data, snap.mime_type or "application/octet-stream"
