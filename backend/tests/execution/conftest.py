"""tests/execution shared fixtures：SQLite 会话 + 两用户 + task + run + events + API TestClient。"""

from __future__ import annotations

from datetime import datetime

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.domain.models import DomainEvent, Run, Task
from app.domain.repository import TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(tmp_path) -> DbSession:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'execution.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def user_a(db: DbSession) -> User:
    return UserRepository(db).create("alice@example.com", "hash", None)


@pytest.fixture()
def user_b(db: DbSession) -> User:
    return UserRepository(db).create("bob@example.com", "hash", None)


@pytest.fixture()
def task_a(db: DbSession, user_a: User) -> Task:
    return TaskRepository(db).create(user_id=user_a.id, title="seed", task_type="directed")


@pytest.fixture()
def client(tmp_path) -> dict:
    from app.auth.deps import get_login_limiter
    from app.auth.rate_limit import InMemoryLoginLimiter
    from app.infra.deps import get_db
    from app.main import create_app
    from fastapi.testclient import TestClient

    engine = create_engine(
        f"sqlite:///{tmp_path / 'execution_api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory}
    app.dependency_overrides.clear()


def _seed_run(factory, user_id: int, task_id: int) -> int:
    session = factory()
    try:
        run = Run(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            plan_version=1,
            state="RUNNING",
        )
        session.add(run)
        session.commit()
        return run.id
    finally:
        session.close()


def _seed_events(factory, user_id: int, task_id: int, run_id: int) -> None:
    """覆盖各阶段的真实事件；occurred_at 显式错开以验证稳定排序。"""
    session = factory()
    try:
        events = [
            ("task.submit", {"command": "submit", "from_state": "DRAFT", "to_state": "QUEUED"}),
            ("task.plan_generated", {"plan_version": 1, "validation_status": "VALID"}),
            (
                "discovery.candidates_found",
                {"candidate_sites": 3, "candidates": 12, "provider": "search"},
            ),
            ("fetch.completed", {"tool": "http", "status": "SUCCESS"}),
            (
                "fetch.failed",
                {"tool": "http", "status": "FAILED", "error_code": "network_timeout"},
            ),
            (
                "extraction.llm_fallback_used",
                {"model": "gpt-4o", "field": "company", "tokens_in": 120, "tokens_out": 40},
            ),
            ("record.approved", {"record_id": 1, "task_id": task_id}),
            (
                "task.complete",
                {"command": "complete", "from_state": "RUNNING", "to_state": "COMPLETED"},
            ),
        ]
        for i, (event_type, payload) in enumerate(events):
            session.add(
                DomainEvent(
                    user_id=user_id,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    event_type=event_type,
                    aggregate_version=1,
                    payload=payload,
                    run_id=run_id,
                    occurred_at=datetime(2026, 8, 12, 0, 0, i),
                )
            )
        session.commit()
    finally:
        session.close()
