"""FieldEvidence / ExtractorRuleVersion repositories (owner-safe, single-txn flush)."""
from __future__ import annotations

import pytest
from app.domain.models import FieldEvidence
from app.extraction.contracts import RecordPartition
from app.extraction.repository import (
    ExtractionRepository,
    ExtractorRuleRepository,
    FieldEvidenceRepository,
)


@pytest.mark.asyncio
async def test_evidence_and_record_commit_in_one_txn(ctx):
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    repo = ExtractionRepository(db)
    record = repo.create_record(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        url_resource_id=None,
        payload={"values": {"公司名": "深圳测试公司"}, "snapshot_id": 1},
    )
    assert record.partition == RecordPartition.EXTRACTED.value
    assert record.id  # flushed, not committed

    ev = FieldEvidenceRepository(db).create(
        record_id=record.id,
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        snapshot_id=1,
        url_resource_id=None,
        field_name="公司名",
        value="深圳测试公司",
        normalized_value="深圳测试公司",
        value_type="text",
        source_url="http://fixture.test/",
        source_locator="jsonld[0]/name",
        raw_snippet="深圳测试公司",
        extract_method="json_ld",
        extractor_version="m11.1",
        rule_version_id=None,
        model_config_id=None,
        confidence=0.95,
        evidence_hash="h1",
        validation_status="valid",
        issue_code=None,
    )
    db.commit()  # single txn commit

    row = db.get(FieldEvidence, ev.id)
    assert row.record_id == record.id
    assert row.task_id == task.id
    assert row.raw_snippet == "深圳测试公司"


def test_evidence_unique_per_field_method(ctx):
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    repo = ExtractionRepository(db)
    record = repo.create_record(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        url_resource_id=None,
        payload={"values": {}, "snapshot_id": 1},
    )
    db.commit()
    ev_repo = FieldEvidenceRepository(db)
    ev_repo.create(
        record_id=record.id,
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        snapshot_id=1,
        url_resource_id=None,
        field_name="官网",
        value="https://a.com",
        normalized_value="https://a.com",
        value_type="url",
        source_url="http://fixture.test/",
        source_locator="meta",
        raw_snippet="a",
        extract_method="meta",
        extractor_version="m11.1",
        rule_version_id=None,
        model_config_id=None,
        confidence=0.9,
        evidence_hash="h2",
        validation_status="valid",
        issue_code=None,
    )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        ev_repo.create(
            record_id=record.id,
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            spec_version=1,
            snapshot_id=1,
            url_resource_id=None,
            field_name="官网",
            value="https://a.com",
            normalized_value="https://a.com",
            value_type="url",
            source_url="http://fixture.test/",
            source_locator="meta",
            raw_snippet="a",
            extract_method="meta",
            extractor_version="m11.1",
            rule_version_id=None,
            model_config_id=None,
            confidence=0.9,
            evidence_hash="h3",
            validation_status="valid",
            issue_code=None,
        )
        db.flush()  # 触发唯一约束（uq_fe_record_field_method）
    db.rollback()


def test_rule_version_immutable_append(ctx):
    db = ctx["db"]
    user = ctx["user"]
    repo = ExtractorRuleRepository(db)
    v1 = repo.create(
        user_id=user.id,
        site_host="fixture.test",
        field_name="公司名",
        schema_identity="name",
        rule_type="css",
        selector="h1.company",
        version=1,
        status="ACTIVE",
    )
    repo.create(
        user_id=user.id,
        site_host="fixture.test",
        field_name="公司名",
        schema_identity="name",
        rule_type="css",
        selector="div.name",
        version=2,
        status="DRAFT",
        supersedes_version_id=v1.id,
    )
    db.commit()
    assert repo.next_version(user_id=user.id, site_host="fixture.test", field_name="公司名") == 3
    active = repo.active_for_fields(
        user_id=user.id, site_host="fixture.test", field_names=["公司名"]
    )
    assert [r.version for r in active] == [1]


def test_snapshot_already_extracted(ctx):
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    repo = ExtractionRepository(db)
    assert repo.snapshot_already_extracted(user.id, task.id, snapshot_id=1) is False
    repo.create_record(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        url_resource_id=None,
        payload={"snapshot_id": 1},
    )
    db.commit()
    assert repo.snapshot_already_extracted(user.id, task.id, snapshot_id=1) is True
