"""Deduplicate/Validate executor 绑定 + 单事务幂等（CORE TEST B 的 retry 语义覆盖）。"""

from __future__ import annotations

import asyncio

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.domain.repository import RunRepository, SpecVersionRepository, TaskRepository
from app.extraction.repository import ExtractionRepository, FieldEvidenceRepository
from app.infra.db import Base
from app.plan.executors import NODE_EXECUTORS
from app.plan.nodes import NodeType
from app.validation.executors import install_validation_executors
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_install_registers_deduplicate_and_validate():
    install_validation_executors()
    assert NodeType.DEDUPLICATE in NODE_EXECUTORS
    assert NodeType.VALIDATE in NODE_EXECUTORS
    assert callable(NODE_EXECUTORS[NodeType.DEDUPLICATE])
    assert callable(NODE_EXECUTORS[NodeType.VALIDATE])


def _spec_payload() -> dict:
    return {
        "task_type": "SPECIFIED_SOURCE",
        "goal": "m12 exec",
        "fields": [
            {"name": "公司名", "type": "text", "required": True},
            {"name": "官网", "type": "url", "required": True},
        ],
        "source_scope": {
            "mode": "SPECIFIED_SOURCE",
            "seed_urls": ["http://fixture.test/"],
            "source_hints": [],
        },
        "completion_conditions": [{"kind": "min_records", "target": 1}],
        "advanced_settings": {},
    }


@pytest.fixture()
def vctx():
    from app.auth.repository import UserRepository

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = UserRepository(db).create("exec@example.com", "hash", None)
    task = TaskRepository(db).create(
        user_id=user.id, title="M-12 exec", task_type="SPECIFIED_SOURCE"
    )
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=_spec_payload(),
    )
    yield {"db": db, "user": user, "task": task, "run": run}
    db.close()


def _seed_record_with_evidence(vctx):
    db = vctx["db"]
    rec = ExtractionRepository(db).create_record(
        user_id=vctx["user"].id,
        task_id=vctx["task"].id,
        run_id=vctx["run"].id,
        spec_version=1,
        url_resource_id=None,
        payload={
            "values": {"公司名": "Acme", "官网": "https://acme.com"},
            "snapshot_id": 1,
            "url": "http://fixture.test/",
            "unresolved_fields": [],
            "issues": [],
        },
    )
    db.flush()
    for field, value in [("公司名", "Acme"), ("官网", "https://acme.com")]:
        FieldEvidenceRepository(db).create(
            record_id=rec.id,
            user_id=vctx["user"].id,
            task_id=vctx["task"].id,
            run_id=vctx["run"].id,
            spec_version=1,
            snapshot_id=1,
            url_resource_id=None,
            field_name=field,
            value=value,
            normalized_value=value,
            value_type="text" if field == "公司名" else "url",
            source_url="http://fixture.test/",
            source_locator=None,
            raw_snippet=value,
            extract_method="json_ld",
            extractor_version="m11.1",
            rule_version_id=None,
            model_config_id=None,
            confidence=0.99,
            evidence_hash="h" * 64,
            validation_status="valid",
            issue_code=None,
        )
    db.commit()
    return rec


def test_validate_executor_single_transaction_idempotent(vctx):
    from app.validation.executor import ValidateNodeExecutor
    from app.validation.repository import ValidationRepository

    rec = _seed_record_with_evidence(vctx)
    db = vctx["db"]
    unit = ExecutionUnit(
        run_id=vctx["run"].id,
        index=1,
        unit_type="validate",
        input_fingerprint="fp",
        node_type="validate",
    )

    async def _run():
        return await ValidateNodeExecutor(db).execute(unit)

    r1 = asyncio.run(_run())
    db.expire_all()
    r2 = asyncio.run(_run())
    assert r1.status == "OK" and r2.status == "OK"
    # 幂等：同一 validation_version 重试不重复 ValidationResult 计数
    counts = ValidationRepository(db).count_by_partition(
        user_id=vctx["user"].id, task_id=vctx["task"].id
    )
    assert counts.get("passed", 0) == 1
    assert counts.get("needs_review", 0) == 0
    # Record 状态迁移：EXTRACTED → passed（已持久化最终分区）
    db.expire_all()
    from app.domain.models import Record

    persisted = db.get(Record, rec.id)
    assert persisted.partition == "passed"
    assert persisted.validated_at is not None


def test_validate_executor_partitions_by_evidence_gate(vctx):
    """无 FieldEvidence 的必填字段 → 不能 PASSED（NEEDS_REVIEW）。"""
    from app.validation.executor import ValidateNodeExecutor
    from app.validation.repository import ValidationRepository

    db = vctx["db"]
    ExtractionRepository(db).create_record(
        user_id=vctx["user"].id,
        task_id=vctx["task"].id,
        run_id=vctx["run"].id,
        spec_version=1,
        url_resource_id=None,
        payload={
            "values": {"公司名": "Acme", "官网": "https://acme.com"},
            "snapshot_id": 1,
            "url": "http://fixture.test/",
            "unresolved_fields": [],
            "issues": [],
        },
    )
    db.commit()
    unit = ExecutionUnit(
        run_id=vctx["run"].id,
        index=1,
        unit_type="validate",
        input_fingerprint="fp",
        node_type="validate",
    )
    asyncio.run(ValidateNodeExecutor(db).execute(unit))
    counts = ValidationRepository(db).count_by_partition(
        user_id=vctx["user"].id, task_id=vctx["task"].id
    )
    assert counts.get("passed", 0) == 0
    assert counts.get("needs_review", 0) == 1  # EVIDENCE_MISSING → NEEDS_REVIEW


def test_dedupe_executor_bounds_long_business_key(vctx):
    """长业务键（>500 chars）不再触发 StringDataRightTruncation；identity 仍由 fingerprint 承担。"""
    from app.domain.models import DedupeCluster
    from app.domain.spec import FieldSpec
    from app.validation.dedupe import (
        BusinessKeyPolicy,
        bounded_business_key,
        business_key_fingerprint,
        compute_business_key,
    )
    from app.validation.executor import DeduplicateNodeExecutor

    fields = [
        FieldSpec(name="公司名", type="text", required=True),
        FieldSpec(name="官网", type="url", required=True),
    ]
    db = vctx["db"]
    long_name = "长" * 600
    ExtractionRepository(db).create_record(
        user_id=vctx["user"].id,
        task_id=vctx["task"].id,
        run_id=vctx["run"].id,
        spec_version=1,
        url_resource_id=None,
        payload={
            "values": {"公司名": long_name, "官网": "https://acme.com"},
            "snapshot_id": 1,
            "url": "http://fixture.test/",
            "unresolved_fields": [],
            "issues": [],
        },
    )
    db.commit()
    unit = ExecutionUnit(
        run_id=vctx["run"].id,
        index=1,
        unit_type="deduplicate",
        input_fingerprint="fp",
        node_type="deduplicate",
    )
    result = asyncio.run(DeduplicateNodeExecutor(db).execute(unit))
    assert result.status == "OK"
    db.expire_all()
    cluster = db.query(DedupeCluster).one()
    assert len(cluster.business_key) <= 500
    # 完整 canonical key 的 fingerprint 仍是 identity（未被截断污染）
    full_key = compute_business_key(
        {"公司名": long_name, "官网": "https://acme.com"},
        BusinessKeyPolicy(key_fields=["公司名", "官网"]),
        fields,
    )
    assert cluster.business_key == bounded_business_key(full_key or "")
    assert cluster.business_key_fingerprint == business_key_fingerprint(full_key or "")
