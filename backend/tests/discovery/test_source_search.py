"""M-09 Task 5: SourceSearch — candidate-site merge + stable missing-config error."""

from __future__ import annotations

import pytest
from app.discovery.source_search import (
    SEARCH_PROVIDER_NOT_CONFIGURED,
    SearchService,
    SourceSearchError,
    merge_into_candidate_sites,
)
from app.providers.search_protocol import SearchResult


def test_merge_search_results_into_candidate_sites() -> None:
    results = [
        SearchResult(
            url="https://example.com/a", title="A", snippet="s", provider="p", rank=1, query="q"
        ),
        SearchResult(
            url="https://example.com/b", title="B", snippet="s", provider="p", rank=2, query="q"
        ),
        SearchResult(
            url="https://other.com/x", title="X", snippet="s", provider="p", rank=3, query="q"
        ),
    ]
    sites = merge_into_candidate_sites(results)
    hosts = {s.site_host for s in sites}
    assert hosts == {"example.com", "other.com"}
    example = next(s for s in sites if s.site_host == "example.com")
    assert len(example.evidence) == 2  # 每条搜索证据都保留


class _EmptyConfigRepo:
    def list_current(self, user_id: int):
        return []


def test_missing_search_config_is_stable_error() -> None:
    service = SearchService(None, vault=object(), search_configs=_EmptyConfigRepo())
    with pytest.raises(SourceSearchError) as exc:
        service._require_config(1)
    assert exc.value.code == SEARCH_PROVIDER_NOT_CONFIGURED
