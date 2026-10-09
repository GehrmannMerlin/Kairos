from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.agent.deps import KairosAgentDeps
from app.agent.tools import commit_extraction, get_collection_progress
from app.collection import validate_record_submission
from app.domain import (
    CollectionError,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionSpecConfirm,
    CollectionSpecVersion,
    CommitExtractionInput,
    EvidenceSubmission,
    RecordStatus,
    RecordSubmission,
)
from app.repositories import (
    confirm_collection_spec,
    create_task_run,
    get_collection_source_for_run,
    get_collection_spec,
    insert_task,
    persist_page_snapshot,
)
from app.storage import InMemoryObjectStore
from pydantic_ai import RunContext


def _spec() -> CollectionSpecVersion:
    now = datetime.now(UTC)
    return CollectionSpecVersion(
        spec_version_id="spec-a",
        owner_id="owner-a",
        task_id="task-a",
        version=1,
        mode=CollectionMode.SPECIFIED_SOURCE,
        goal="extract",
        fields=[
            CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True),
            CollectionFieldSpec(name="count", type=CollectionFieldType.INTEGER, required=False),
            CollectionFieldSpec(name="published_on", type=CollectionFieldType.DATE, required=False),
        ],
        seed_urls=["https://example.com/page"],
        confirmed_at=now,
        created_at=now,
    )


def test_unknown_extraction_field_is_rejected() -> None:
    with pytest.raises(CollectionError, match="UNKNOWN_FIELD"):
        validate_record_submission(
            _spec(),
            "Title: Kairos",
            RecordSubmission(
                fields={"title": "Kairos", "unknown": "value"},
                evidence={"title": EvidenceSubmission(quote="Title: Kairos")},
            ),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("count", True),
        ("published_on", "2026-99-99"),
    ],
)
def test_invalid_field_type_is_deterministically_needs_review(field_name: str, value: object) -> None:
    result = validate_record_submission(
        _spec(),
        "Title: Kairos",
        RecordSubmission(
            fields={"title": "Kairos", field_name: value},
            evidence={
                "title": EvidenceSubmission(quote="Title: Kairos"),
                field_name: EvidenceSubmission(quote="Title: Kairos"),
            },
        ),
    )

    assert result.status is RecordStatus.NEEDS_REVIEW
    assert "INVALID_FIELD_TYPE" in result.validation_issues


def test_missing_required_field_is_needs_review_without_fabricating_value() -> None:
    result = validate_record_submission(
        _spec(),
        "Title: Kairos",
        RecordSubmission(
            fields={"count": 1},
            evidence={"count": EvidenceSubmission(quote="Title: Kairos")},
        ),
    )

    assert result.status is RecordStatus.NEEDS_REVIEW
    assert result.data_json == {"count": 1}
    assert "MISSING_REQUIRED_FIELD" in result.validation_issues


def test_matching_evidence_is_verified_and_false_quote_needs_review() -> None:
    passed = validate_record_submission(
        _spec(),
        "Title: Kairos",
        RecordSubmission(
            fields={"title": "Kairos"},
            evidence={"title": EvidenceSubmission(quote="Title: Kairos", confidence=0.9)},
        ),
    )
    review = validate_record_submission(
        _spec(),
        "Title: Kairos",
        RecordSubmission(
            fields={"title": "Kairos"},
            evidence={"title": EvidenceSubmission(quote="Not in snapshot")},
        ),
    )

    assert passed.status is RecordStatus.PASSED
    assert passed.evidence[0].verified is True
    assert review.status is RecordStatus.NEEDS_REVIEW
    assert review.evidence[0].verified is False
    assert "EVIDENCE_QUOTE_NOT_FOUND" in review.validation_issues


def test_empty_record_is_rejected_and_quote_is_bounded() -> None:
    empty = validate_record_submission(_spec(), "Title: Kairos", RecordSubmission())

    assert empty.status is RecordStatus.REJECTED
    assert "EMPTY_RECORD" in empty.validation_issues
    with pytest.raises(ValueError):
        EvidenceSubmission(quote="x" * 1001)


async def _snapshot_context() -> tuple[RunContext[KairosAgentDeps], str, InMemoryObjectStore]:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="extract",
            fields=[CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://example.com/page"],
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "collect")
    source = await get_collection_source_for_run(
        task_id, run_id, owner_id, spec.spec_version_id, "https://example.com/page"
    )
    assert source is not None
    store = InMemoryObjectStore()
    await store.put_bytes("raw-key", b"<title>Kairos</title>", "text/html")
    await store.put_bytes("text-key", b"Title: Kairos", "text/plain")
    await persist_page_snapshot(
        source_id=source.source_id,
        owner_id=owner_id,
        task_id=task_id,
        task_run_id=run_id,
        url=source.url,
        canonical_url=source.canonical_url,
        status_code=200,
        content_type="text/html",
        title="Kairos",
        content_hash="hash-extract",
        raw_storage_key="raw-key",
        text_storage_key="text-key",
        bytes_read=20,
        text_chars=13,
        text_preview="Title: Kairos",
    )
    source = await get_collection_source_for_run(
        task_id, run_id, owner_id, spec.spec_version_id, "https://example.com/page"
    )
    assert source is not None and source.snapshot_id is not None
    context = RunContext[object](
        deps=KairosAgentDeps(
            user_id=owner_id,
            task_id=task_id,
            task_run_id=run_id,
            spec_version_id=spec.spec_version_id,
            model_config_id="test-model",
        ),
        model=None,
        usage=None,
        run_id=run_id,
    )
    return context, source.snapshot_id, store


@pytest.mark.asyncio
async def test_search_snippet_without_snapshot_cannot_create_record() -> None:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="collect frameworks",
            fields=[CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://example.com"],
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "collect")
    context = RunContext(
        deps=KairosAgentDeps(
            user_id=owner_id,
            task_id=task_id,
            task_run_id=run_id,
            spec_version_id=spec.spec_version_id,
            model_config_id="test-model",
        ),
        model=None,
        usage=None,
        run_id=run_id,
    )

    with pytest.raises(CollectionError, match="SNAPSHOT_NOT_FOUND"):
        await commit_extraction(
            context,
            CommitExtractionInput(
                snapshot_id="search-result-without-snapshot",
                records=[
                    RecordSubmission(
                        fields={"name": "FastAPI"},
                        evidence={
                            "name": EvidenceSubmission(quote="FastAPI is a modern high-performance framework")
                        },
                    )
                ],
            ),
        )


@pytest.mark.asyncio
async def test_empty_extraction_commit_marks_source_processed_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, snapshot_id, store = await _snapshot_context()
    monkeypatch.setattr("app.agent.tools.get_object_store", lambda: store)

    first = await commit_extraction(ctx, CommitExtractionInput(snapshot_id=snapshot_id, records=[]))
    second = await commit_extraction(ctx, CommitExtractionInput(snapshot_id=snapshot_id, records=[]))
    progress = await get_collection_progress(ctx)

    assert first.record_count == 0
    assert second.idempotent is True
    assert second.extraction_commit_id == first.extraction_commit_id
    assert progress.processed_sources == 1
    assert progress.remaining_sources == 0


@pytest.mark.asyncio
async def test_extraction_commit_rejects_a_different_payload_for_same_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, snapshot_id, store = await _snapshot_context()
    monkeypatch.setattr("app.agent.tools.get_object_store", lambda: store)
    await commit_extraction(ctx, CommitExtractionInput(snapshot_id=snapshot_id, records=[]))

    with pytest.raises(CollectionError, match="ALREADY_COMMITTED_DIFFERENT_PAYLOAD"):
        await commit_extraction(
            ctx,
            CommitExtractionInput(
                snapshot_id=snapshot_id,
                records=[
                    RecordSubmission(
                        fields={"title": "Kairos"},
                        evidence={"title": EvidenceSubmission(quote="Title: Kairos")},
                    )
                ],
            ),
        )
