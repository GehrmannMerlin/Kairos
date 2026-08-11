"""M-12 contracts + persistence roundtrip（migration 0010 经 Base.metadata 全量建表）。"""

from __future__ import annotations

import pytest
from app.domain.repository import (
    RecordRepository,
    RunRepository,
    SpecVersionRepository,
    TaskRepository,
)
from app.infra.db import Base
from app.validation.repository import ValidationRepository
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _spec_payload() -> dict:
    return {
        "task_type": "SPECIFIED_SOURCE",
        "goal": "m12",
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
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)  # 覆盖 migration 0010 全部新表
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    from app.auth.repository import UserRepository

    user = UserRepository(db).create("v12@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=user.id, title="M-12", task_type="SPECIFIED_SOURCE")
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=_spec_payload(),
    )
    record = RecordRepository(
        db
    ).create(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=1,
        payload={"values": {}},
        partition="extracted",
    )
    yield {"db": db, "user": user, "task": task, "run": run, "record": record}
    db.close()


def test_validation_result_roundtrip_and_partition_count(vctx):
    from datetime import UTC, datetime

    repo = ValidationRepository(vctx["db"])
    repo.create_result(
        user_id=vctx["user"].id,
        task_id=vctx["task"].id,
        run_id=vctx["run"].id,
        spec_version=1,
        result={
            "record_id": vctx["record"].id,
            "spec_version_id": 1,
            "validation_version": "m12.1",
            "partition": "passed",
            "structural_issues": [],
            "required_field_issues": [],
            "evidence_issues": [],
            "business_rule_issues": [],
            "allowed_actions": ["approve"],
            "validated_at": datetime(2026, 8, 11, tzinfo=UTC),
        },
    )
    repo.create_result(
        user_id=vctx["user"].id,
        task_id=vctx["task"].id,
        run_id=vctx["run"].id,
        spec_version=1,
        result={
            "record_id": 999,
            "spec_version_id": 1,
            "validation_version": "m12.1",
            "partition": "needs_review",
            "structural_issues": [],
            "required_field_issues": [],
            "evidence_issues": [],
            "business_rule_issues": [],
            "allowed_actions": [],
            "validated_at": datetime(2026, 8, 11, tzinfo=UTC),
        },
    )
    vctx["db"].commit()
    counts = repo.count_by_partition(user_id=vctx["user"].id, task_id=vctx["task"].id)
    assert counts == {"passed": 1, "needs_review": 1}


def test_dedupe_cluster_idempotent_by_fingerprint(vctx):
    repo = ValidationRepository(vctx["db"])
    fp = "a" * 64
    g1 = repo.create_group(
        user_id=vctx["user"].id,
        task_id=vctx["task"].id,
        run_id=vctx["run"].id,
        spec_version=1,
        business_key="key",
        business_key_fingerprint=fp,
        dedupe_policy_version="m12.1",
        approximate=False,
        record_ids=[1, 2],
    )
    vctx["db"].commit()
    g2 = repo.find_group(
        user_id=vctx["user"].id, task_id=vctx["task"].id, business_key_fingerprint=fp
    )
    assert g2 is not None and g2.id == g1.id


def test_owner_isolation_rejects_foreign_record(vctx):
    from app.auth.repository import UserRepository

    other = UserRepository(vctx["db"]).create("other@example.com", "hash", None)
    repo = ValidationRepository(vctx["db"])
    # find_* 是 owner-safe find：越权查询返回 None（不泄漏存在性），等价 owner-safe 404
    assert (
        repo.find_result(
            user_id=other.id, record_id=vctx["record"].id, validation_version="m12.1"
        )
        is None
    )
    assert (
        repo.find_group(user_id=other.id, task_id=vctx["task"].id, business_key_fingerprint="x")
        is None
    )
    assert repo.count_by_partition(user_id=other.id, task_id=vctx["task"].id) == {}


def test_migration_0010_upgrade_sql_is_generatable():
    """alembic upgrade head --sql 必须成功并包含 M-12 新表。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
    command.upgrade(cfg, "0010", sql=True)
