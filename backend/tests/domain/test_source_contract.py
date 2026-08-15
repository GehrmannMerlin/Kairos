"""Deterministic source admission tests."""

from __future__ import annotations

import pytest
from app.discovery.errors import DiscoveryValidationError
from app.domain.source_contract import normalize_source_contract
from app.domain.spec import SourceScope
from app.domain.task_types import TaskType


def test_explicit_url_becomes_specified_source() -> None:
    result = normalize_source_contract(
        task_type=TaskType.HYBRID,
        source_scope=SourceScope(
            mode=TaskType.HYBRID,
            seed_urls=["HTTPS://Example.COM/a/../notice#top"],
            source_hints=["示例官网"],
        ),
        search_available=False,
        explicit_texts=(),
    )
    assert result.ready is True
    assert result.task_type is TaskType.SPECIFIED_SOURCE
    assert result.source_scope.seed_urls == ["https://example.com/notice"]
    assert result.issue_code is None


def test_named_source_with_search_becomes_scoped_hybrid() -> None:
    result = normalize_source_contract(
        task_type=TaskType.SPECIFIED_SOURCE,
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=[],
            source_hints=["山东省人民政府官网"],
        ),
        search_available=True,
        explicit_texts=(),
    )
    assert result.ready is True
    assert result.task_type is TaskType.HYBRID
    assert result.resolution_scope == "NAMED_SOURCE_ONLY"


def test_named_source_without_search_requires_url() -> None:
    result = normalize_source_contract(
        task_type=TaskType.SPECIFIED_SOURCE,
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=[],
            source_hints=["山东省人民政府官网"],
        ),
        search_available=False,
        explicit_texts=(),
    )
    assert result.ready is False
    assert result.issue_code == "SOURCE_RESOLUTION_REQUIRED"
    assert result.clarification_question == "请提供该网站的完整网址，或先配置可用的搜索服务。"


def test_literal_url_in_user_text_survives_model_omission() -> None:
    result = normalize_source_contract(
        task_type=TaskType.SPECIFIED_SOURCE,
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=[],
            source_hints=["山东省人民政府官网"],
        ),
        search_available=False,
        explicit_texts=("请采集 https://www.shandong.gov.cn/ 的公示",),
    )
    assert result.ready is True
    assert result.task_type is TaskType.SPECIFIED_SOURCE
    assert result.source_scope.seed_urls == ["https://www.shandong.gov.cn/"]


@pytest.mark.parametrize(
    "url, message",
    [
        ("ftp://example.com/notice", "不支持的 scheme"),
        ("https://user:password@example.com/notice", "URL 不允许包含用户信息"),
    ],
)
def test_unsafe_seed_url_is_rejected(url: str, message: str) -> None:
    with pytest.raises(DiscoveryValidationError, match=message):
        normalize_source_contract(
            task_type=TaskType.SPECIFIED_SOURCE,
            source_scope=SourceScope(mode=TaskType.SPECIFIED_SOURCE, seed_urls=[url]),
            search_available=False,
        )


def test_duplicate_canonical_urls_are_deduplicated() -> None:
    result = normalize_source_contract(
        task_type=TaskType.SPECIFIED_SOURCE,
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=["HTTPS://Example.COM/a/../notice#top"],
        ),
        search_available=False,
        explicit_texts=("也请采集 https://example.com/notice",),
    )
    assert result.source_scope.seed_urls == ["https://example.com/notice"]
