"""Goal Understanding API + MODEL_NOT_CONFIGURED persistence (TEST B/C)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.agents.deps import get_goal_understanding_service
from app.agents.goal_understanding import GoalInput
from app.agents.schemas import GoalUnderstandingResult
from app.agents.service import GoalUnderstandingService
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.config import Settings, get_settings
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.domain.models import UnderstandingAttempt
from app.domain.spec import SourceScope
from app.domain.task_types import TaskType
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from app.providers.errors import ProviderNetworkError
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


def _exploratory_result() -> GoalUnderstandingResult:
    return GoalUnderstandingResult(
        task_type=TaskType.EXPLORATORY,
        goal="搜集深圳的工业自动化设备供应商",
        confidence=0.9,
    )


def _specified_result_without_url() -> GoalUnderstandingResult:
    return GoalUnderstandingResult(
        task_type=TaskType.SPECIFIED_SOURCE,
        goal="采集指定网站公示",
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            source_hints=["山东省人民政府官网"],
        ),
        confidence=0.9,
    )


def _result_with_invalid_source_url() -> GoalUnderstandingResult:
    return GoalUnderstandingResult(
        task_type=TaskType.SPECIFIED_SOURCE,
        goal="采集指定网站公示",
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=["ftp://example.com/notice"],
        ),
        confidence=0.9,
    )


class FakeAgent:
    def __init__(self, result: GoalUnderstandingResult) -> None:
        self._result = result
        self.called_with: list[GoalInput] = []
        self.resolved_seen = None
        self.api_key_seen = None

    async def understand(
        self, *, goal_input, chat_context, resolved, api_key
    ) -> GoalUnderstandingResult:
        self.called_with.append(goal_input)
        self.resolved_seen = resolved
        self.api_key_seen = api_key
        return self._result


class FailingAgent:
    async def understand(self, *, goal_input, chat_context, resolved, api_key):
        raise ProviderNetworkError("fixture transport failure")


def _make_app(
    factory,
    *,
    fake_result: GoalUnderstandingResult | None = None,
    agent_override=None,
):
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
    app.dependency_overrides[get_settings] = lambda: Settings(
        credential_master_key="00" * 32,
        otel_enabled=False,
    )

    if fake_result is not None or agent_override is not None:

        def _override_service(db: DbSession = Depends(get_db)):
            vault = CredentialVault(
                master_key=b"\x00" * 32,
                key_version="k1",
                repository=CredentialRepository(db),
            )
            provider = ProviderService(
                vault=vault,
                model_configs=ModelConfigRepository(db),
                search_configs=SearchConfigRepository(db),
            )
            return GoalUnderstandingService(
                db,
                provider_service=provider,
                vault=vault,
                agent=agent_override or FakeAgent(fake_result),
            )

        app.dependency_overrides[get_goal_understanding_service] = _override_service

    return app, limiter


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'understand.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app, limiter = _make_app(factory, fake_result=_exploratory_result())
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory, "limiter": limiter}
    app.dependency_overrides.clear()


@pytest.fixture()
def client_no_model(tmp_path):
    """Default dependency chain (real agent) but no ModelConfig -> MODEL_NOT_CONFIGURED."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'nomodel.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app, limiter = _make_app(factory, fake_result=None)
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory, "limiter": limiter}
    app.dependency_overrides.clear()


@pytest.fixture()
def client_provider_failure(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'provider-failure.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app, limiter = _make_app(factory, agent_override=FailingAgent())
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory, "limiter": limiter}
    app.dependency_overrides.clear()


@pytest.fixture()
def client_model_omits_url(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'model-omits-url.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app, limiter = _make_app(factory, fake_result=_specified_result_without_url())
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory, "limiter": limiter}
    app.dependency_overrides.clear()


@pytest.fixture()
def client_invalid_model_source(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'invalid-model-source.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app, limiter = _make_app(factory, fake_result=_result_with_invalid_source_url())
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory, "limiter": limiter}
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_available_model(factory, user_id: int) -> str:
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
        return cfg.config_id
    finally:
        session.close()


def test_understand_produces_typed_result_and_spec_draft(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    user = _register(c, "alice@example.com")["user"]
    _seed_available_model(factory, user["id"])

    created = c.post("/api/tasks", json={"content": "帮我搜集深圳的工业自动化设备供应商"})
    task_id = created.json()["task_id"]

    resp = c.post(f"/api/tasks/{task_id}/understand")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["result"]["task_type"] == "EXPLORATORY"
    assert data["spec_draft"]["goal"] == "搜集深圳的工业自动化设备供应商"
    assert data["message"]["role"] == "assistant"
    assert data["message"]["ref_type"] == "goal_result"
    assert data["message"]["meta"]["provider"] == "openai"

    # task.task_type set; spec draft persisted; chat has user + assistant
    shell = c.get(f"/api/tasks/{task_id}")
    assert shell.json()["task_type"] == "EXPLORATORY"
    draft = c.get(f"/api/tasks/{task_id}/spec-draft")
    assert draft.json()["payload"]["task_type"] == "EXPLORATORY"
    chat = c.get(f"/api/tasks/{task_id}/chat")
    roles = [m["role"] for m in chat.json()["messages"]]
    assert roles == ["user", "assistant"]


def test_understand_preserves_literal_user_url_omitted_by_model(
    client_model_omits_url: dict,
) -> None:
    c, factory = client_model_omits_url["client"], client_model_omits_url["factory"]
    user = _register(c, "alice@example.com")["user"]
    _seed_available_model(factory, user["id"])
    created = c.post(
        "/api/tasks",
        json={"content": "请采集 https://www.shandong.gov.cn/ 的任前公示"},
    )

    response = c.post(f"/api/tasks/{created.json()['task_id']}/understand")

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["task_type"] == "SPECIFIED_SOURCE"
    assert result["source_scope"]["seed_urls"] == ["https://www.shandong.gov.cn/"]


def test_understand_invalid_model_url_marks_attempt_failed(
    client_invalid_model_source: dict,
) -> None:
    c, factory = client_invalid_model_source["client"], client_invalid_model_source["factory"]
    user = _register(c, "alice@example.com")["user"]
    _seed_available_model(factory, user["id"])
    created = c.post("/api/tasks", json={"content": "请采集指定网站公示"})
    task_id = created.json()["task_id"]

    response = c.post(f"/api/tasks/{task_id}/understand")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "PROVIDER_INFERENCE_ERROR"
    session = factory()
    try:
        attempt = session.query(UnderstandingAttempt).filter_by(task_id=task_id).one()
        assert attempt.status == "failed"
        assert attempt.error_code == "PROVIDER_INFERENCE_ERROR"
    finally:
        session.close()


def test_understand_model_not_configured_preserves_input(client_no_model: dict) -> None:
    c = client_no_model["client"]
    _register(c, "alice@example.com")

    created = c.post("/api/tasks", json={"content": "帮我搜集深圳的工业自动化设备供应商"})
    task_id = created.json()["task_id"]

    resp = c.post(f"/api/tasks/{task_id}/understand")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "MODEL_NOT_CONFIGURED"

    # Draft + user message must survive (D-066).
    chat = c.get(f"/api/tasks/{task_id}/chat")
    assert chat.status_code == 200
    messages = chat.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "深圳" in messages[0]["content"]


def test_provider_failure_preserves_one_task_and_one_user_message(
    client_provider_failure: dict,
) -> None:
    c, factory = client_provider_failure["client"], client_provider_failure["factory"]
    user = _register(c, "alice@example.com")["user"]
    _seed_available_model(factory, user["id"])

    created = c.post("/api/tasks", json={"content": "帮我采集上海市政府任前公示"})
    task_id = created.json()["task_id"]
    failed = c.post(f"/api/tasks/{task_id}/understand")

    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "NETWORK_ERROR"
    tasks = c.get("/api/tasks").json()["tasks"]
    assert [task["task_id"] for task in tasks] == [task_id]
    messages = c.get(f"/api/tasks/{task_id}/chat").json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert sum(message["role"] == "user" for message in messages) == 1
    assert messages[1]["ref_type"] == "error"
    assert messages[1]["meta"]["error_code"] == "NETWORK_ERROR"
