"""M-07 集成测试共享 fixtures（连接本地栈真实 PostgreSQL/Temporal）。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.auth.repository import UserRepository
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.domain.spec import FieldSpec, SpecDraftPayload
from app.domain.task_types import TaskType
from app.infra.deps import get_session_factory


@pytest.fixture()
def confirmed_task() -> dict:
    """注册 Gate user + 创建 DRAFT Task + confirm_spec 冻结 spec v1（无需真实模型）。"""
    session = get_session_factory()()
    try:
        email = f"m07-gate-{uuid4().hex[:12]}@kairos.test"
        user = UserRepository(session).create(email, "hash", None)
        task = TaskRepository(session).create(
            user_id=user.id, title="M07 gate task", task_type=None
        )
        spec = SpecDraftPayload(
            task_type=TaskType.EXPLORATORY,
            goal="搜集深圳工业自动化设备供应商",
            fields=[FieldSpec(name="公司名", type="text", required=True)],
        )
        DomainService(TaskRepository(session)).confirm_spec(
            user_id=user.id,
            task_id=task.id,
            expected_version=task.version,
            spec_payload=spec.model_dump(mode="json"),
            actor_id=user.id,
        )
        task = TaskRepository(session).get_owned(user.id, task.id)
        assert task.state == "QUEUED"
        return {"user_id": user.id, "task_id": task.id, "spec_version": 1}
    finally:
        session.close()
