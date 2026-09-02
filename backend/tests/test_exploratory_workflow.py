from __future__ import annotations

from app.domain import (
    CollectionExecutionContext,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
)
from app.workflows import _collection_prompt


def test_exploratory_prompt_requires_progress_search_and_snapshot_evidence() -> None:
    context = CollectionExecutionContext(
        spec_version_id="spec-exploratory",
        mode=CollectionMode.EXPLORATORY,
        goal="Find projects",
        fields=[CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True)],
        sources=[],
        target_count=5,
    )

    prompt = _collection_prompt("find five", context)

    assert "search_sources" in prompt
    assert "Search snippet is not evidence" in prompt
    assert "EXPLORATORY" in prompt
    assert "target_count" in prompt
