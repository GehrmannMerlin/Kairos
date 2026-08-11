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
    repo.mark_state(user_id=1, url_hash=h, state=FrontierState.READY_FOR_FETCH)
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
