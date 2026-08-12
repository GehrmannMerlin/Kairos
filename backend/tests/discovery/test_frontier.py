"""M-09 Task 4: persistent URL Frontier — canonical dedupe + idempotent replay."""

from __future__ import annotations

import pytest
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import DiscoveryEvidence, DiscoverySource, FrontierState
from app.domain.models import URLResource
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db():
    from app.infra.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_duplicate_canonical_url_is_single_entry(db) -> None:
    repo = UrlFrontierRepository(db)
    h1, created1 = repo.upsert_discovery(
        task_id=1,
        user_id=1,
        run_id=1,
        spec_version=1,
        raw_url="https://Example.com:443/x#frag",
        source=DiscoverySource.USER_SEED,
    )
    h2, created2 = repo.upsert_discovery(
        task_id=1,
        user_id=1,
        run_id=1,
        spec_version=1,
        raw_url="https://example.com/x",
        source=DiscoverySource.SEARCH_RESULT,
        evidence=DiscoveryEvidence(source=DiscoverySource.SEARCH_RESULT, query="k"),
    )
    assert created1 is True
    assert created2 is False
    assert h1 == h2
    count = db.query(URLResource).filter(URLResource.task_id == 1).count()
    assert count == 1
    row = db.query(URLResource).filter(URLResource.task_id == 1).first()
    assert row.discovery_count == 2  # 重复发现累加计数，不产生第二个有效 Entry


def test_state_transition_to_ready_for_fetch(db) -> None:
    repo = UrlFrontierRepository(db)
    h, _ = repo.upsert_discovery(
        task_id=2,
        user_id=1,
        run_id=1,
        spec_version=1,
        raw_url="https://example.com/page",
        source=DiscoverySource.SITEMAP,
    )
    repo.mark_state(user_id=1, task_id=2, url_hash=h, state=FrontierState.READY_FOR_FETCH)
    ready = repo.list_ready_for_fetch(user_id=1, task_id=2)
    assert len(ready) == 1
    assert ready[0].url_hash == h


def test_replay_after_restart_resumes_without_duplicate(db) -> None:
    """幂等重放：同 batch 重试不产生第二个有效 Entry，也不丢已提交状态。"""
    repo = UrlFrontierRepository(db)
    h, created = repo.upsert_discovery(
        task_id=3,
        user_id=1,
        run_id=1,
        spec_version=1,
        raw_url="https://example.com/a",
        source=DiscoverySource.USER_SEED,
    )
    assert created is True
    # Worker 重启后重跑同一批次
    repo2 = UrlFrontierRepository(db)
    h2, created2 = repo2.upsert_discovery(
        task_id=3,
        user_id=1,
        run_id=1,
        spec_version=1,
        raw_url="https://example.com/a",
        source=DiscoverySource.USER_SEED,
    )
    assert h2 == h
    assert created2 is False
    assert db.query(URLResource).filter(URLResource.task_id == 3).count() == 1
    assert db.query(URLResource).filter(URLResource.task_id == 3).first().discovery_count == 2


def test_state_mark_is_scoped_to_task_when_seed_shared_across_tasks(db) -> None:
    """DEPLOY-GATE-3 blocker 回归：同一 canonical seed 被多个 Task 共享时，
    mark_state 必须只修改当前 Task 的 URLResource（唯一约束是 task_id + url_hash）。

    access_rules.execute 先 list_by_state(task_id, DISCOVERED) 拿到当前 Task 的 row，
    再以 user_id + task_id + url_hash 调 mark_state。若 _owned 缺少 task_id 过滤，
    会选中最早 Task 的同 hash 行，当前 Task 的行永远停在 DISCOVERED。
    """
    repo = UrlFrontierRepository(db)
    # 三个 Task 共享同一 canonical seed URL（url_hash 相同，DB 身份各自独立）
    task_ids = [30, 31, 32]
    for task_id in task_ids:
        h, _ = repo.upsert_discovery(
            task_id=task_id,
            user_id=1,
            run_id=task_id * 10,
            spec_version=1,
            raw_url="https://example.com/seed",
            source=DiscoverySource.USER_SEED,
        )
        assert len(
            repo.list_by_state(user_id=1, task_id=task_id, state=FrontierState.DISCOVERED)
        ) == 1
    assert db.query(URLResource).filter(URLResource.url_hash == h).count() == 3

    # 每个 Task 各自 mark_state(ACCESS_ALLOWED)，只能影响自己的行
    for task_id in task_ids:
        row = repo.list_by_state(user_id=1, task_id=task_id, state=FrontierState.DISCOVERED)[0]
        repo.mark_state(
            user_id=1,
            task_id=row.task_id,
            url_hash=row.url_hash,
            state=FrontierState.ACCESS_ALLOWED,
        )
    for task_id in task_ids:
        allowed = repo.list_by_state(
            user_id=1, task_id=task_id, state=FrontierState.ACCESS_ALLOWED
        )
        assert len(allowed) == 1
        assert allowed[0].task_id == task_id
        # 其他 Task 的行不受本次 mark 影响（各 Task 恰好一行 ACCESS_ALLOWED）
        assert len(
            repo.list_by_state(user_id=1, task_id=task_id, state=FrontierState.DISCOVERED)
        ) == 0

    # mark_fetch_outcome 同样按 task 隔离：只把 Task 31 的 ACCESS_ALLOWED → FETCHED
    repo.mark_fetch_outcome(
        user_id=1, task_id=31, url_hash=h, state=FrontierState.FETCHED, error_code=None
    )
    assert len(repo.list_by_state(user_id=1, task_id=31, state=FrontierState.FETCHED)) == 1
    assert len(repo.list_by_state(user_id=1, task_id=30, state=FrontierState.ACCESS_ALLOWED)) == 1
    assert len(repo.list_by_state(user_id=1, task_id=32, state=FrontierState.ACCESS_ALLOWED)) == 1

    # mark_blocked 同样按 task 隔离：只把 Task 32 的 ACCESS_ALLOWED → BLOCKED
    repo.mark_blocked(user_id=1, task_id=32, url_hash=h, reason="regression_scope_check")
    assert len(repo.list_by_state(user_id=1, task_id=32, state=FrontierState.BLOCKED)) == 1
    assert len(repo.list_by_state(user_id=1, task_id=30, state=FrontierState.ACCESS_ALLOWED)) == 1
    assert len(repo.list_by_state(user_id=1, task_id=31, state=FrontierState.FETCHED)) == 1
