"""ExtractionContextBuilder — from immutable PageSnapshot to bounded safe context.

Never sends the full 5MB HTML to an extractor/LLM (二十八：上下文最小化)。
user_id 由 immutable snapshot 的 owner 推导，绝不信任外部输入。
"""

from __future__ import annotations

from typing import Any

from parsel import Selector

from app.crawling.contracts import PageSnapshotRef
from app.domain.models import PageSnapshot
from app.domain.spec import FieldSpec
from app.extraction.contracts import ExtractionSettings
from app.extraction.protocol import ExtractionContext
from app.infra.object_storage import ObjectStorage


class ExtractionContextBuilder:
    def __init__(
        self, db: Any, storage: ObjectStorage, settings: ExtractionSettings | None = None
    ) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings or ExtractionSettings()

    async def build(self, snapshot: PageSnapshot, spec_payload: dict) -> ExtractionContext:
        html = await self._load_html(snapshot)
        bounded_html = html[: self._settings.max_context_bytes]
        return ExtractionContext(
            snapshot_ref=self._to_ref(snapshot),
            spec_payload=spec_payload,
            fields=self._parse_fields(spec_payload),
            readable_text=self._readable_text(bounded_html),
            html=bounded_html,
            user_id=snapshot.user_id,
            db=self._db,
            settings=self._settings,
        )

    async def _load_html(self, snapshot: PageSnapshot) -> str:
        if not snapshot.storage_ref:
            return ""
        raw = await self._storage.get(snapshot.storage_ref)
        return raw.decode("utf-8", errors="ignore")

    @staticmethod
    def _to_ref(snapshot: PageSnapshot) -> PageSnapshotRef:
        return PageSnapshotRef(
            snapshot_id=snapshot.id,
            content_hash=snapshot.content_hash,
            storage_ref=snapshot.storage_ref or "",
            url=snapshot.final_url or "",
            final_url=snapshot.final_url or "",
            tool=snapshot.tool,
            tool_version=snapshot.tool_version,
            mime_type=snapshot.mime_type,
            spec_version=snapshot.spec_version,
            run_id=snapshot.run_id or 0,
            url_resource_id=snapshot.url_resource_id,
            fetched_at=snapshot.captured_at.isoformat() if snapshot.captured_at else "",
        )

    @staticmethod
    def _parse_fields(spec_payload: dict) -> tuple[FieldSpec, ...]:
        parsed: list[FieldSpec] = []
        for f in spec_payload.get("fields") or []:
            try:
                parsed.append(FieldSpec.model_validate(f))
            except Exception:
                continue
        return tuple(parsed)

    def _readable_text(self, html: str) -> str:
        if not html:
            return ""
        sel = Selector(text=html)
        parts = sel.xpath(
            "//text()[not(ancestor::script) and not(ancestor::style)]"
        ).getall()
        text = " ".join(" ".join(parts).split())
        return text[: self._settings.max_context_chars]
