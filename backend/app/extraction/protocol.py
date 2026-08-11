"""Extractor protocol + ExtractionContext (one shared typed contract).

ExtractionContext carries the bounded context every extractor needs; the
``user_id`` is set by the context builder from the immutable snapshot's owner
(it is never trusted from an input that could cross users).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.crawling.contracts import PageSnapshotRef
from app.domain.spec import FieldSpec
from app.extraction.contracts import ExtractionResult, ExtractionSettings


@dataclass(frozen=True)
class ExtractionContext:
    snapshot_ref: PageSnapshotRef
    spec_payload: dict
    fields: tuple[FieldSpec, ...]
    readable_text: str
    html: str
    user_id: int | None = None
    db: Any = None
    settings: ExtractionSettings = field(default_factory=ExtractionSettings)


class Extractor(Protocol):
    name: str
    version: str

    async def extract(
        self, ctx: ExtractionContext, *, unresolved: list[str]
    ) -> ExtractionResult: ...
