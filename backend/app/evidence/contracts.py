"""M-14 Evidence Query typed contracts（D-056/D-064）。

EvidenceView 是历史快照事实：来源 URL/fetch time/version/field evidence/display mode。
只读；绝不重新请求 source_url。不含 MinIO key 等内部存储引用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class EvidenceFieldEvidenceDto(BaseModel):
    model_config = _STRICT

    record_id: int
    field_name: str
    value: str | None = None
    raw_snippet: str | None = None
    source_locator: str | None = None
    extract_method: str | None = None
    extractor_version: str | None = None
    confidence: float | None = None


class EvidenceView(BaseModel):
    """evidence_id = PageSnapshot.id（与 Record Drawer 的 snapshot_id 对齐）。"""

    model_config = _STRICT

    evidence_id: int
    task_id: int
    source_url: str
    fetched_at: datetime | None = None
    snapshot_version: int = 1
    tool: str = "http"
    tool_version: str = "unknown"
    mime_type: str | None = None
    http_status: int | None = None
    content_length: int | None = None
    display_mode: Literal["snapshot", "text", "raw"] = "raw"
    summary: str | None = None
    field_evidence: list[EvidenceFieldEvidenceDto] = Field(default_factory=list)
    has_content: bool = False
    download_url: str = ""
