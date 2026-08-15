"""Deterministic, network-free source admission for specs and Goal Understanding."""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel

from app.discovery.url import canonical_url
from app.domain.spec import SourceScope
from app.domain.task_types import TaskType


class SourceResolutionScope(StrEnum):
    NAMED_SOURCE_ONLY = "NAMED_SOURCE_ONLY"


class SourceContractResult(BaseModel):
    ready: bool
    task_type: TaskType
    source_scope: SourceScope
    resolution_scope: SourceResolutionScope | None = None
    issue_code: str | None = None
    clarification_question: str | None = None


def normalize_source_contract(
    *,
    task_type: TaskType,
    source_scope: SourceScope,
    search_available: bool,
    explicit_texts: Sequence[str] = (),
) -> SourceContractResult:
    """Resolve literal URLs and named-source hints without network access."""
    explicit_urls = [
        match.rstrip("，。；、)]}>")
        for text in explicit_texts
        for match in re.findall(r"https?://[^\s<>\'\"]+", text, flags=re.IGNORECASE)
    ]
    seed_urls = list(
        dict.fromkeys(canonical_url(raw) for raw in [*source_scope.seed_urls, *explicit_urls])
    )
    source_hints = list(dict.fromkeys(h.strip() for h in source_scope.source_hints if h.strip()))
    if seed_urls:
        return SourceContractResult(
            ready=True,
            task_type=TaskType.SPECIFIED_SOURCE,
            source_scope=SourceScope(
                mode=TaskType.SPECIFIED_SOURCE,
                seed_urls=seed_urls,
                source_hints=source_hints,
            ),
        )
    if source_hints and search_available:
        return SourceContractResult(
            ready=True,
            task_type=TaskType.HYBRID,
            source_scope=SourceScope(
                mode=TaskType.HYBRID,
                seed_urls=[],
                source_hints=source_hints,
                resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY.value,
            ),
            resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY,
        )
    if source_hints:
        return SourceContractResult(
            ready=False,
            task_type=TaskType.HYBRID,
            source_scope=SourceScope(
                mode=TaskType.HYBRID,
                seed_urls=[],
                source_hints=source_hints,
                resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY.value,
            ),
            resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY,
            issue_code="SOURCE_RESOLUTION_REQUIRED",
            clarification_question="请提供该网站的完整网址，或先配置可用的搜索服务。",
        )
    return SourceContractResult(
        ready=True,
        task_type=TaskType.EXPLORATORY,
        source_scope=SourceScope(mode=TaskType.EXPLORATORY),
    )
