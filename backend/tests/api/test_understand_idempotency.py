"""Goal Understanding server-side idempotency（request-lifecycle 修复）。

覆盖：
- 相同输入并发两个 /understand：Provider 只调一次，第二个 IN_PROGRESS；
- RUNNING 重复请求不调 Provider；
- SUCCEEDED reload 复用已有结果，不调 Provider、不产生重复 goal_result；
- FAILED 自动 reload 不自动重试；
- 用户显式「重新理解」（USER_REUNDERSTAND）允许新 attempt；
- trigger_source / 审计 metadata 不泄露 Secret。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from app.agents.goal_understanding import GoalInput
from app.agents.schemas import GoalUnderstandingResult
from app.agents.service import TRIGGER_USER_REUNDERSTAND, GoalUnderstandingService
from app.auth.deps import get_login_limiter
from app.auth.models import User
from app.auth.rate_limit import InMemoryLoginLimiter
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.domain.models import ChatMessage, UnderstandingAttempt
from app.domain.task_types import TaskType
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from app.providers.errors import ProviderNetworkError
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _result() -> GoalUnderstandingResult:
    return GoalUnderstandingResult(
        task_type=TaskType.EXPLORATORY,
        goal="搜集深圳的工业自动化设备供应商",
        confidence=0.9,
    )


class FakeAgent:
    def __init__(self, result: GoalUnderstandingResult) -> None:
        self._result = result
        self.called_with: list[GoalInput] = []

    async def understand(
        self, *, goal_input, chat_context, resolved, api_key
    ) -> GoalUnderstandingResult:
        self.called_with.append(goal_input)
        return self._result


class GatedAgent(FakeAgent):
    """第一个调用阻塞直到 release，用于制造并发重叠窗口。"""

    def __init__(self, result: GoalUnderstandingResult) -> None:
        super().__init__(result)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def understand(
        self, *, goal_input, chat_context, resolved, api_key
    ) -> GoalUnderstandingResult:
        self.called_with.append(goal_input)
        self.started.set()
        await self.release.wait()
        return self._result


class FailingAgent:
    async def understand(self, *, goal_input, chat_context, resolved, api_key):
        raise ProviderNetworkError("fixture transport failure")


def _make_app(factory):
    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: InMemoryLoginLimiter(
        max_attempts=3, window_seconds=100
    )
    return app


def _register(c: TestClient, email: str) -> dict:
    resp = c.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_available_model(factory, user_id: int) -> None:
    session = factory()
    try:
        repo = ModelConfigRepository(session)
        cfg = repo.create_version(
            user_id=user_id,
            name="测试模型",
            provider_type="openai",
            model_name="gpt-4o-mini",
            base_url=None,
            credential_version_id=None,
            is_default=True,
        )
        repo.mark_connection(user_id, cfg.config_id, "available", datetime.now(UTC))
    finally:
        session.close()


def _make_service_for(factory, agent) -> GoalUnderstandingService:
    session = factory()
    vault = CredentialVault(
        master_key=b"\x00" * 32, key_version="k1", repository=CredentialRepository(session)
    )
    provider = ProviderService(
        vault=vault,
        model_configs=ModelConfigRepository(session),
        search_configs=SearchConfigRepository(session),
    )
    return GoalUnderstandingService(session, provider_service=provider, vault=vault, agent=agent)


def _create_task_with_user(factory, c: TestClient, email: str) -> tuple[int, int]:
    user = _register(c, email)["user"]
    _seed_available_model(factory, user["id"])
    created = c.post("/api/tasks", json={"content": "帮我搜集深圳的工业自动化设备供应商"})
    assert created.status_code == 201, created.text
    return created.json()["task_id"], user["id"]


def _count_goal_results(factory, task_id: int) -> int:
    session = factory()
    try:
        return int(
            session.query(ChatMessage)
            .filter(
                ChatMessage.task_id == task_id,
                ChatMessage.role == "assistant",
                ChatMessage.ref_type == "goal_result",
            )
            .count()
        )
    finally:
        session.close()


def _attempts(factory, task_id: int) -> list[UnderstandingAttempt]:
    session = factory()
    try:
        return list(session.query(UnderstandingAttempt).filter_by(task_id=task_id).all())
    finally:
        session.close()


@pytest.fixture()
def engine_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'understand-idem.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_input_calls_provider_once(engine_factory) -> None:
    factory = engine_factory
    app = _make_app(factory)
    with TestClient(app) as c:
        task_id, user_id = _create_task_with_user(factory, c, "alice@example.com")

    session = factory()
    user = session.get(User, user_id)
    session.close()

    agent = GatedAgent(_result())
    service_a = _make_service_for(factory, agent)
    service_b = _make_service_for(factory, agent)

    coro_a = service_a.understand_for_task(
        user=user, task_id=task_id, trigger_source="AUTO_INITIAL"
    )
    task_a = asyncio.ensure_future(coro_a)
    await asyncio.wait_for(agent.started.wait(), timeout=5)  # A 已提交 RUNNING + 进入模型调用

    outcome_b = await service_b.understand_for_task(
        user=user, task_id=task_id, trigger_source="AUTO_INITIAL"
    )
    assert outcome_b.status == "IN_PROGRESS"

    agent.release.set()
    outcome_a = await asyncio.wait_for(task_a, timeout=5)
    assert outcome_a.status == "SUCCEEDED"

    assert len(agent.called_with) == 1  # Provider 只调一次
    assert _count_goal_results(factory, task_id) == 1  # 只一个 goal_result


@pytest.mark.asyncio
async def test_running_repeat_returns_in_progress_without_model_call(engine_factory) -> None:
    factory = engine_factory
    app = _make_app(factory)
    with TestClient(app) as c:
        task_id, user_id = _create_task_with_user(factory, c, "alice@example.com")

    session = factory()
    user = session.get(User, user_id)
    session.close()

    agent = GatedAgent(_result())
    service = _make_service_for(factory, agent)
    coro = service.understand_for_task(user=user, task_id=task_id, trigger_source="USER_SEND")
    pending = asyncio.ensure_future(coro)
    await asyncio.wait_for(agent.started.wait(), timeout=5)

    # 第二个 USER_SEND 自动触发：RUNNING → IN_PROGRESS，不调 Provider
    outcome = await service.understand_for_task(
        user=user, task_id=task_id, trigger_source="USER_SEND"
    )
    assert outcome.status == "IN_PROGRESS"
    assert len(agent.called_with) == 1

    agent.release.set()
    await asyncio.wait_for(pending, timeout=5)


@pytest.mark.asyncio
async def test_succeeded_reload_reuses_existing_result(engine_factory) -> None:
    factory = engine_factory
    app = _make_app(factory)
    with TestClient(app) as c:
        task_id, user_id = _create_task_with_user(factory, c, "alice@example.com")

    session = factory()
    user = session.get(User, user_id)
    session.close()

    agent = FakeAgent(_result())
    service = _make_service_for(factory, agent)

    first = await service.understand_for_task(
        user=user, task_id=task_id, trigger_source="AUTO_INITIAL"
    )
    assert first.status == "SUCCEEDED"

    reload = await service.understand_for_task(
        user=user, task_id=task_id, trigger_source="AUTO_INITIAL"
    )
    assert reload.status == "ALREADY_SUCCEEDED"
    assert reload.spec_draft == first.spec_draft
    assert reload.result == first.result
    assert len(agent.called_with) == 1  # reload 不再调 Provider
    assert _count_goal_results(factory, task_id) == 1  # 不产生重复 goal_result

    # 再次 reload（浏览器刷新）依然复用
    again = await service.understand_for_task(user=user, task_id=task_id, trigger_source="RECOVERY")
    assert again.status == "ALREADY_SUCCEEDED"
    assert len(agent.called_with) == 1


@pytest.mark.asyncio
async def test_explicit_reunderstand_allows_new_attempt(engine_factory) -> None:
    factory = engine_factory
    app = _make_app(factory)
    with TestClient(app) as c:
        task_id, user_id = _create_task_with_user(factory, c, "alice@example.com")

    session = factory()
    user = session.get(User, user_id)
    session.close()

    agent = FakeAgent(_result())
    service = _make_service_for(factory, agent)

    await service.understand_for_task(user=user, task_id=task_id, trigger_source="AUTO_INITIAL")
    second = await service.understand_for_task(
        user=user, task_id=task_id, trigger_source=TRIGGER_USER_REUNDERSTAND
    )
    assert second.status == "SUCCEEDED"
    assert len(agent.called_with) == 2  # 用户显式重跑允许新模型调用
    attempts = _attempts(factory, task_id)
    assert [a.trigger_source for a in attempts] == ["AUTO_INITIAL", TRIGGER_USER_REUNDERSTAND]


@pytest.mark.asyncio
async def test_failed_auto_retry_is_blocked(engine_factory) -> None:
    factory = engine_factory
    app = _make_app(factory)
    with TestClient(app) as c:
        task_id, user_id = _create_task_with_user(factory, c, "alice@example.com")

    session = factory()
    user = session.get(User, user_id)
    session.close()

    agent = FailingAgent()
    service = _make_service_for(factory, agent)

    with pytest.raises(ProviderNetworkError):
        await service.understand_for_task(user=user, task_id=task_id, trigger_source="AUTO_INITIAL")

    # 自动 reload：复用失败分类，不再调 Provider（agent 仍是 FailingAgent，若被调用会再抛）
    with pytest.raises(ProviderNetworkError):
        await service.understand_for_task(user=user, task_id=task_id, trigger_source="AUTO_INITIAL")

    attempts = _attempts(factory, task_id)
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
    assert attempts[0].error_code == "NETWORK_ERROR"

    # 用户显式重跑允许新 attempt
    second = _make_service_for(factory, FailingAgent())
    with pytest.raises(ProviderNetworkError):
        await second.understand_for_task(
            user=user, task_id=task_id, trigger_source=TRIGGER_USER_REUNDERSTAND
        )
    assert len(_attempts(factory, task_id)) == 2


@pytest.mark.asyncio
async def test_attempt_audit_has_no_secret(engine_factory) -> None:
    factory = engine_factory
    app = _make_app(factory)
    with TestClient(app) as c:
        task_id, user_id = _create_task_with_user(factory, c, "alice@example.com")

    session = factory()
    user = session.get(User, user_id)
    session.close()

    agent = FakeAgent(_result())
    service = _make_service_for(factory, agent)
    outcome = await service.understand_for_task(
        user=user, task_id=task_id, trigger_source="USER_SEND"
    )

    attempts = _attempts(factory, task_id)
    assert len(attempts) == 1
    row = attempts[0]
    assert row.trigger_source == "USER_SEND"
    assert row.model_config_id and row.provider == "openai" and row.model
    assert outcome.audit is not None
    # 审计只含安全 metadata；attempt 表不允许存在明文 secret 字段
    secret_fields = {"api_key", "secret", "token", "password", "authorization"}
    assert not (secret_fields & set(row.__dict__.keys()))
    assert "sk-" not in str({k: v for k, v in row.__dict__.items() if v is not None})
    assert "Bearer" not in str(outcome.audit)
