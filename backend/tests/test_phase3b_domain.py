from __future__ import annotations

import pytest
from app.domain import (
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionSourceStatus,
    CollectionSpecConfirm,
    SearchLimits,
)
from pydantic import ValidationError


def _field() -> CollectionFieldSpec:
    return CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True)


def test_exploratory_spec_allows_empty_seed_urls_and_uses_bounded_defaults() -> None:
    spec = CollectionSpecConfirm(
        goal="Find projects",
        fields=[_field()],
        seed_urls=[],
        target_count=5,
        mode=CollectionMode.EXPLORATORY,
    )

    assert spec.mode is CollectionMode.EXPLORATORY
    assert spec.search_limits == SearchLimits()
    assert spec.scope_domains == []


def test_hybrid_spec_requires_seed_or_scope() -> None:
    with pytest.raises(ValidationError, match="seed URL or scope domain"):
        CollectionSpecConfirm(
            goal="Find projects",
            fields=[_field()],
            seed_urls=[],
            target_count=5,
            mode=CollectionMode.HYBRID,
        )


def test_specified_source_requires_seed_but_does_not_require_target_or_search() -> None:
    with pytest.raises(ValidationError, match="seed URL"):
        CollectionSpecConfirm(
            goal="Extract",
            fields=[_field()],
            seed_urls=[],
            mode=CollectionMode.SPECIFIED_SOURCE,
        )

    spec = CollectionSpecConfirm(
        goal="Extract",
        fields=[_field()],
        seed_urls=["https://example.com"],
        mode=CollectionMode.SPECIFIED_SOURCE,
    )
    assert spec.target_count is None


def test_search_limits_reject_values_above_server_hard_maximum() -> None:
    with pytest.raises(ValidationError):
        SearchLimits(max_search_rounds=11)
    with pytest.raises(ValidationError):
        SearchLimits(max_results_per_round=21)
    with pytest.raises(ValidationError):
        SearchLimits(max_discovered_sources=101)
    with pytest.raises(ValidationError):
        SearchLimits(max_processed_sources=101)


def test_skipped_is_a_terminal_collection_source_status() -> None:
    assert CollectionSourceStatus.SKIPPED.value == "SKIPPED"
