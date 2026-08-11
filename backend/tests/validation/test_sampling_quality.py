"""CORE TEST D（质量指标 DB 一致性）+ CORE TEST E（分层抽样代表性与稳定性）。"""

from __future__ import annotations

import pytest
from app.auth.repository import UserRepository
from app.domain.models import URLResource
from app.domain.repository import RunRepository, TaskRepository
from app.infra.db import Base
from app.validation.sampling import SamplingPolicy, StratifiedSampler
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _record(rid, values):
    class _R:
        pass

    r = _R()
    r.id, r.payload = rid, {"values": values}
    return r


def test_sampling_representative_strata_and_stable_identity():
    policy = SamplingPolicy(sample_size_per_stratum=2)
    sampler = StratifiedSampler(policy)
    recs = [_record(i, {"v": i}) for i in range(1, 21)]
    # 分布独立：source=奇偶，method=i%3，confidence=(i//4)%2，保证 2×2×2=8 层齐全
    facts = {
        i: {
            "source": "SEARCH_RESULT" if i % 2 else "SITEMAP",
            "method": "json_ld" if i % 3 else "llm",
            "rule_version": None,
            "confidence": 0.95 if (i // 4) % 2 == 0 else 0.4,
        }
        for i in range(1, 21)
    }
    sample1, fp1 = sampler.select(recs, facts)
    sample2, fp2 = sampler.select(recs, facts)
    assert fp1 == fp2  # 同 policy/version 稳定（模块需求 37）
    assert sample1 == sample2
    # 所有关键分层组合（source × method × rule_version × confidence band）都有 representation
    strata_seen = {s["stratum"] for s in sample1}
    expected = {
        str((src, mtd, "none", band))
        for src in ("SEARCH_RESULT", "SITEMAP")
        for mtd in ("json_ld", "llm")
        for band in ("high", "low")
    }
    assert expected <= strata_seen


@pytest.fixture()
def qctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = UserRepository(db).create("q12@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=user.id, title="M-12 q", task_type="EXPLORATORY")
    run = RunRepository(db).create(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    yield {"db": db, "user": user, "task": task, "run": run}
    db.close()


def test_quality_metrics_match_db_facts(qctx):
    from datetime import UTC, datetime

    from app.validation.repository import ValidationRepository

    db = qctx["db"]
    repo = ValidationRepository(db)
    # 8 条固定 dataset：3 passed / 3 needs_review（missing+duplicate+conflict）/ 2 rejected
    for rid, part, rtype in [
        (1, "passed", None),
        (2, "passed", None),
        (3, "passed", None),
        (4, "needs_review", "missing_required"),
        (5, "needs_review", "possible_duplicate"),
        (6, "needs_review", "unresolved_conflict"),
        (7, "rejected", None),
        (8, "rejected", None),
    ]:
        repo.create_result(
            user_id=qctx["user"].id,
            task_id=qctx["task"].id,
            run_id=qctx["run"].id,
            spec_version=1,
            result={
                "record_id": rid,
                "spec_version_id": 1,
                "validation_version": "m12.1",
                "partition": part,
                "structural_issues": [],
                "required_field_issues": [],
                "evidence_issues": [],
                "business_rule_issues": [],
                "review_type": rtype,
                "allowed_actions": [],
                "validated_at": datetime(2026, 8, 11, tzinfo=UTC),
            },
        )
    db.add(
        URLResource(
            user_id=qctx["user"].id,
            task_id=qctx["task"].id,
            url="http://a",
            url_hash="h1",
            source_type="USER_SEED",
            status="HANDED_OFF",
        )
    )
    db.add(
        URLResource(
            user_id=qctx["user"].id,
            task_id=qctx["task"].id,
            url="http://b",
            url_hash="h2",
            source_type="SITEMAP",
            status="DISCOVERED",
        )
    )
    # 未裁决 FieldConflict 是 conflict_count 的数据库事实
    repo.create_conflict(
        user_id=qctx["user"].id,
        task_id=qctx["task"].id,
        record_id=6,
        dedupe_group_id=None,
        field_name="主营产品",
        candidate_values=["产品A", "产品B"],
        resolution=None,
        state="unresolved",
    )
    db.commit()
    from app.validation.quality import QualityMetricsService

    out = QualityMetricsService().compute(
        db,
        user_id=qctx["user"].id,
        task_id=qctx["task"].id,
        run_id=qctx["run"].id,
        spec_version=1,
        validation_version="m12.1",
        dataset_version="v1",
        sampling_policy_version="m12.1",
        sample_refs=[],
    )
    m = out["metrics"]
    assert m["needs_review_count"] == 3
    assert m["rejected_count"] == 2
    assert m["pass_rate"] == round(3 / 8, 4)  # passed=3 / total=8
    assert m["missing_rate"] == round(1 / 8, 4)
    assert m["duplicate_rate"] == round(1 / 8, 4)
    assert m["conflict_count"] == 1
    assert m["source_coverage"] == 0.5  # covered=USER_SEED / eligible=USER_SEED+SITEMAP


def test_quality_sampling_accuracy_from_known_answers(qctx):
    from app.validation.quality import QualityMetricsService

    out = QualityMetricsService().compute(
        qctx["db"],
        user_id=qctx["user"].id,
        task_id=qctx["task"].id,
        run_id=qctx["run"].id,
        spec_version=1,
        validation_version="m12.1",
        dataset_version="v1",
        sampling_policy_version="m12.1",
        sample_refs=[{"record_id": 1}, {"record_id": 2}, {"record_id": 3}, {"record_id": 4}],
        known_answers={1: {}, 2: {}, 4: {}},  # 3/4 命中
    )
    assert out["metrics"]["sampling_accuracy"] == 0.75
