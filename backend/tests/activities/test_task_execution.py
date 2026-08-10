"""M-07 task lifecycle activity tests (SQLite)."""

from __future__ import annotations

import app.activities.task_execution as task_execution
import pytest
from app.activities.task_execution import FailRunInput, fail_run
from app.auth.models import User
from app.domain.models import Run, Task
from app.domain.repository import RunRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.asyncio
async def test_fail_run_marks_task_and_run_failed(monkeypatch, tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'activities.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)

    session = factory()
    try:
        user = User(email="fail@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="fail me", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=0
        )
        # 直接置为 RUNNING（不经状态机，聚焦 fail 命令的收尾行为）
        task.state = "RUNNING"
        run.state = "running"
        session.commit()
        task_id = task.id
        run_id = run.id
        user_id = user.id
    finally:
        session.close()

    await fail_run(FailRunInput(task_id=task_id, user_id=user_id, run_id=run_id))

    session = factory()
    try:
        task = session.get(Task, task_id)
        run = session.get(Run, run_id)
        assert task.state == "FAILED"
        assert run.state == "failed"
        assert run.finished_at is not None
    finally:
        session.close()
