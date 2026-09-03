"""Phase 4 collection prompt browser-escalation guidance tests."""

from __future__ import annotations

from app.agent.browser_tool import browser_toolset
from app.agent.runtime import _collection_toolset
from app.domain import (
    CollectionExecutionContext,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionSourceOrigin,
    CollectionSourceStatus,
    CollectionSourceSummary,
)
from app.workflows import _collection_prompt


def _context(mode: CollectionMode) -> CollectionExecutionContext:
    return CollectionExecutionContext(
        spec_version_id="spec-1",
        mode=mode,
        goal="Collect quotes and authors from JS-rendered pages",
        fields=[CollectionFieldSpec(name="quote", type=CollectionFieldType.STRING, required=True)],
        sources=[
            CollectionSourceSummary(
                source_id="src-1",
                url="https://quotes.toscrape.com/js/",
                canonical_url="https://quotes.toscrape.com/js/",
                origin=CollectionSourceOrigin.SEED,
                status=CollectionSourceStatus.PENDING,
                title="Quotes to Scrape",
            )
        ],
        target_count=5 if mode is not CollectionMode.SPECIFIED_SOURCE else None,
    )


def test_specified_source_prompt_has_browser_escalation_guidance() -> None:
    prompt = _collection_prompt("go", _context(CollectionMode.SPECIFIED_SOURCE))
    assert "request_browser_task(source_id)" in prompt
    assert "HTTP FIRST" in prompt
    assert "never fabricate values or evidence" in prompt
    assert "robots-blocked, private" in prompt
    assert "re-inspect that snapshot" in prompt


def test_exploratory_prompt_has_browser_escalation_guidance() -> None:
    prompt = _collection_prompt("go", _context(CollectionMode.EXPLORATORY))
    assert "request_browser_task(source_id)" in prompt
    assert "HTTP FIRST" in prompt
    assert "re-inspect the browser snapshot" in prompt
    assert "robots-blocked, private" in prompt


def _toolset_text(toolset) -> str:
    parts = []
    for item in toolset._instructions:
        if isinstance(item, str):
            parts.append(item)
        else:
            parts.append(str(item))
    return " ".join(parts)


def test_collection_toolset_instructions_mention_browser() -> None:
    """The kairos-collection-v1 toolset teaches the model to escalate only when needed."""
    instruction_text = _toolset_text(_collection_toolset)
    assert "fetch_source" in instruction_text
    assert "inspect_snapshot" in instruction_text


def test_browser_toolset_rejects_bad_escalation_cases_in_instructions() -> None:
    instruction_text = _toolset_text(browser_toolset)
    assert "Never request a browser for robots-blocked, private, 403/401, 404, or captcha" in instruction_text
    assert "Do not use the browser to log in, submit, purchase, upload, or download" in instruction_text