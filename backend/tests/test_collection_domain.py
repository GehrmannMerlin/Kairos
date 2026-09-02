from __future__ import annotations

import pytest
from app.domain import (
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionSpecConfirm,
)
from pydantic import ValidationError


def test_collection_spec_accepts_all_phase_3a_field_types_and_valid_identifiers() -> None:
    fields = [
        CollectionFieldSpec(name="text_value", type=CollectionFieldType.STRING, required=True),
        CollectionFieldSpec(name="whole_number", type=CollectionFieldType.INTEGER, required=False),
        CollectionFieldSpec(name="decimal_value", type=CollectionFieldType.NUMBER, required=False),
        CollectionFieldSpec(name="is_active", type=CollectionFieldType.BOOLEAN, required=False),
        CollectionFieldSpec(name="published_on", type=CollectionFieldType.DATE, required=False),
        CollectionFieldSpec(name="source_url", type=CollectionFieldType.URL, required=False),
    ]

    spec = CollectionSpecConfirm(
        goal="extract page information",
        fields=fields,
        seed_urls=["https://example.com"],
    )

    assert spec.mode is CollectionMode.SPECIFIED_SOURCE
    assert [field.type for field in spec.fields] == list(CollectionFieldType)


@pytest.mark.parametrize("name", ["Title", "1title", "title-name", "", "a" * 65])
def test_collection_field_rejects_unstable_identifiers(name: str) -> None:
    with pytest.raises(ValidationError):
        CollectionFieldSpec(name=name, type=CollectionFieldType.STRING, required=True)


def test_collection_spec_rejects_duplicate_fields_and_empty_collections() -> None:
    field = CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)

    with pytest.raises(ValidationError):
        CollectionSpecConfirm(goal="extract", fields=[field, field], seed_urls=["https://example.com"])
    with pytest.raises(ValidationError):
        CollectionSpecConfirm(goal="extract", fields=[], seed_urls=["https://example.com"])
    with pytest.raises(ValidationError):
        CollectionSpecConfirm(goal="extract", fields=[field], seed_urls=[])


def test_collection_spec_accepts_only_specified_source_mode() -> None:
    field = CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)

    with pytest.raises(ValidationError):
        CollectionSpecConfirm(
            goal="extract",
            fields=[field],
            seed_urls=["https://example.com"],
            mode=CollectionMode.EXPLORATORY,
        )
