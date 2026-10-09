from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.collection import ValidatedEvidence, ValidatedRecord, normalize_record_data, record_identity_key
from app.domain import (
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionSpecConfirm,
    CollectionSpecVersion,
    RecordStatus,
)
from app.repositories import (
    confirm_collection_spec,
    create_task_run,
    get_collection_source_for_run,
    get_collection_spec,
    insert_task,
    list_collection_records,
    list_record_evidence,
    persist_extraction_commit,
    persist_page_snapshot,
)


def _normalization_spec() -> CollectionSpecVersion:
    now = datetime.now(UTC)
    return CollectionSpecVersion(
        spec_version_id="spec-normalize",
        owner_id="owner-normalize",
        task_id="task-normalize",
        version=1,
        mode=CollectionMode.EXPLORATORY,
        goal="normalize",
        fields=[
            CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True),
            CollectionFieldSpec(name="website", type=CollectionFieldType.URL, required=True),
            CollectionFieldSpec(name="published_on", type=CollectionFieldType.DATE, required=False),
            CollectionFieldSpec(name="count", type=CollectionFieldType.INTEGER, required=False),
        ],
        seed_urls=[],
        target_count=1,
        confirmed_at=now,
        created_at=now,
    )


def test_normalization_is_typed_and_stable() -> None:
    spec = _normalization_spec()
    normalized = normalize_record_data(
        spec,
        {
            "name": "  Fast   API\n",
            "website": "HTTPS://Example.com:443/docs#intro",
            "published_on": "2026-08-01",
            "count": 3,
        },
    )

    assert normalized == {
        "name": "Fast API",
        "website": "https://example.com/docs",
        "published_on": "2026-08-01",
        "count": 3,
    }
    assert record_identity_key(spec, normalized) == "website:https://example.com/docs"


async def _dedup_scope() -> tuple[str, str, str, str, list[str]]:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="Collect frameworks",
            fields=[
                CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True),
                CollectionFieldSpec(name="website", type=CollectionFieldType.URL, required=True),
                CollectionFieldSpec(name="description", type=CollectionFieldType.STRING, required=True),
            ],
            seed_urls=["https://example.com/a", "https://example.com/b"],
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "collect")
    snapshot_ids: list[str] = []
    for url in spec.seed_urls:
        source = await get_collection_source_for_run(task_id, run_id, owner_id, spec.spec_version_id, url)
        assert source is not None
        snapshot = await persist_page_snapshot(
            source_id=source.source_id,
            owner_id=owner_id,
            task_id=task_id,
            task_run_id=run_id,
            url=url,
            canonical_url=url,
            status_code=200,
            content_type="text/html",
            title="Framework",
            content_hash=f"hash-{uuid4().hex}",
            raw_storage_key=f"raw-{uuid4().hex}",
            text_storage_key=f"text-{uuid4().hex}",
            bytes_read=100,
            text_chars=100,
            text_preview="Framework",
        )
        snapshot_ids.append(snapshot.snapshot_id)
    return owner_id, task_id, run_id, spec.spec_version_id, snapshot_ids


def _framework_record(description: str) -> ValidatedRecord:
    return ValidatedRecord(
        data_json={
            "name": "Fast API",
            "website": "https://example.com/framework",
            "description": description,
        },
        status=RecordStatus.PASSED,
        validation_issues=[],
        evidence=[
            ValidatedEvidence(field_name="name", quote="Fast API", confidence=0.9, verified=True),
            ValidatedEvidence(
                field_name="website", quote="https://example.com/framework", confidence=0.9, verified=True
            ),
            ValidatedEvidence(field_name="description", quote=description, confidence=0.9, verified=True),
        ],
    )


@pytest.mark.asyncio
async def test_duplicate_observation_and_evidence_are_preserved_but_canonical_count_is_one() -> None:
    owner_id, task_id, run_id, spec_id, snapshot_ids = await _dedup_scope()
    first = await persist_extraction_commit(
        snapshot_id=snapshot_ids[0],
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner_id,
        spec_version_id=spec_id,
        payload_hash="payload-one",
        validated_records=[_framework_record("A framework")],
        source_complete=True,
    )
    second = await persist_extraction_commit(
        snapshot_id=snapshot_ids[1],
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner_id,
        spec_version_id=spec_id,
        payload_hash="payload-two",
        validated_records=[_framework_record("A framework")],
        source_complete=True,
    )

    records = await list_collection_records(task_id, run_id, owner_id, spec_id)
    evidence = await list_record_evidence(first.record_ids[0], task_id, owner_id)

    assert [record.record_id for record in records] == [first.record_ids[0]]
    all_records = await list_collection_records(task_id, run_id, owner_id, spec_id, include_duplicates=True)
    assert len(all_records) == 2
    assert all_records[1].canonical_record_id == first.record_ids[0]
    assert second.record_ids[0] != first.record_ids[0]
    assert len(evidence) == 6


@pytest.mark.asyncio
async def test_conflicting_identity_values_are_retained_for_review() -> None:
    owner_id, task_id, run_id, spec_id, snapshot_ids = await _dedup_scope()
    await persist_extraction_commit(
        snapshot_id=snapshot_ids[0],
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner_id,
        spec_version_id=spec_id,
        payload_hash="payload-conflict-one",
        validated_records=[_framework_record("First description")],
        source_complete=True,
    )
    await persist_extraction_commit(
        snapshot_id=snapshot_ids[1],
        task_id=task_id,
        task_run_id=run_id,
        owner_id=owner_id,
        spec_version_id=spec_id,
        payload_hash="payload-conflict-two",
        validated_records=[_framework_record("Second description")],
        source_complete=True,
    )

    all_records = await list_collection_records(task_id, run_id, owner_id, spec_id, include_duplicates=True)

    assert len(all_records) == 2
    assert all_records[0].status == RecordStatus.NEEDS_REVIEW.value
    assert all_records[1].status == RecordStatus.NEEDS_REVIEW.value
    assert "CONFLICTING_SOURCE_VALUE" in all_records[0].validation_issues
    assert "CONFLICTING_SOURCE_VALUE" in all_records[1].validation_issues
