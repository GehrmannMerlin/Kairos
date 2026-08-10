"""M-06 fake smoke: the whole core chain with a fake agent (TEST G, no external cost).

User A → create Draft + message → fake GoalUnderstandingAgent → EXPLORATORY
Spec Draft → edit a field → confirm → immutable v1 → create Template from Spec →
use Template({city}=深圳) → second Task Draft keeping TemplateVersion ref →
User B access to A Task/Template rejected. No Plan/Temporal/Search/Crawler.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.agents.deps import get_goal_understanding_service
from app.agents.schemas import GoalUnderstandingResult
from app.agents.service import GoalUnderstandingService
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.domain.spec import FieldSpec
from app.domain.task_types import TaskType
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


class FakeAgent:
    async def understand(
        self, *, goal_input, chat_context, resolved, api_key
    ) -> GoalUnderstandingResult:
        return GoalUnderstandingResult(
            task_type=TaskType.EXPLORATORY,
            goal="帮我搜集深圳的工业自动化设备供应商",
            confidence=0.92,
            fields=[FieldSpec(name="公司名", type="text", required=True)],
            auto_expand_fields=True,
            template_variables=[
                {"name": "city", "label": "城市", "value": "深圳"},
                {"name": "note", "label": "备注", "value": "none"},
            ],
        )


@pytest.fixture()
def smoke(tmp_path) -> dict:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'smoke.db'}", connect_args={"check_same_thread": False}
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

    def _override_service(db: DbSession = Depends(get_db)):
        vault = CredentialVault(
            master_key=b"\x00" * 32, key_version="k1", repository=CredentialRepository(db)
        )
        provider = ProviderService(
            vault=vault,
            model_configs=ModelConfigRepository(db),
            search_configs=SearchConfigRepository(db),
        )
        return GoalUnderstandingService(
            db, provider_service=provider, vault=vault, agent=FakeAgent()
        )

    app.dependency_overrides[get_goal_understanding_service] = _override_service
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory}
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_model(factory, user_id: int) -> None:
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


def test_m06_fake_smoke(smoke: dict) -> None:
    c, factory = smoke["client"], smoke["factory"]
    alice = _register(c, "alice@example.com")["user"]
    _seed_model(factory, alice["id"])

    # 1) Workbench: Task Draft + first user message
    created = c.post("/api/tasks", json={"content": "帮我搜集深圳的工业自动化设备供应商"})
    assert created.status_code == 201
    task_id = created.json()["task_id"]
    chat = c.get(f"/api/tasks/{task_id}/chat")
    assert len(chat.json()["messages"]) == 1

    # 2) Goal Understanding → EXPLORATORY Spec Draft
    understood = c.post(f"/api/tasks/{task_id}/understand")
    assert understood.status_code == 200
    assert understood.json()["result"]["task_type"] == "EXPLORATORY"
    draft = understood.json()["spec_draft"]
    assert "深圳" in draft["goal"]
    assert draft["template_variables"][0]["name"] == "city"

    # 3) Edit one field (保存 Draft ≠ 确认)
    draft["fields"].append({"name": "官网", "type": "url", "required": False})
    saved = c.put(f"/api/tasks/{task_id}/spec-draft", json={"payload": draft})
    assert saved.status_code == 200
    assert len(saved.json()["payload"]["fields"]) == 2

    # 4) Confirm → v1, task QUEUED
    v1 = c.post(
        f"/api/tasks/{task_id}/spec-confirm", json={"expected_version": 1, "payload": draft}
    )
    assert v1.status_code == 200, v1.text
    assert v1.json()["spec_version"] == 1
    assert v1.json()["state"] == "QUEUED"
    shell = c.get(f"/api/tasks/{task_id}")
    assert shell.json()["current_spec_version"] == 1

    # 5) Revision → v2 immutable, v1 unchanged (只改非 goal 字段，保留 深圳 供变量化)
    draft["auto_expand_fields"] = False
    v2 = c.post(
        f"/api/tasks/{task_id}/spec-confirm", json={"expected_version": 2, "payload": draft}
    )
    assert v2.status_code == 200
    assert v2.json()["spec_version"] == 2

    # 6) Create Template from Spec → {city} variableized
    tpl = c.post(f"/api/tasks/{task_id}/template")
    assert tpl.status_code == 200
    tpl_data = tpl.json()
    assert "{city}" in tpl_data["goal_template"]
    assert "深圳" not in tpl_data["goal_template"]
    assert any(v["name"] == "city" for v in tpl_data["variables"])

    # 7) Use Template {city}=深圳 → second Task Draft keeping TemplateVersion ref
    used = c.post(
        f"/api/templates/{tpl_data['template_id']}/use", json={"variables": {"city": "深圳"}}
    )
    assert used.status_code == 200
    task2_id = used.json()["task_id"]
    shell2 = c.get(f"/api/tasks/{task2_id}")
    assert shell2.json()["template_id"] == tpl_data["template_id"]
    assert shell2.json()["template_version"] == 1
    draft2 = c.get(f"/api/tasks/{task2_id}/spec-draft")
    assert "深圳" in draft2.json()["payload"]["goal"]

    # 8) User B rejected on both A task and A template (safe 404)
    _register(c, "bob@example.com")
    assert c.get(f"/api/tasks/{task_id}/chat").status_code == 404
    assert c.get(f"/api/tasks/{task2_id}/chat").status_code == 404
    assert c.get(f"/api/templates/{tpl_data['template_id']}").status_code == 404
    assert (
        c.post(
            f"/api/templates/{tpl_data['template_id']}/use", json={"variables": {"city": "X"}}
        ).status_code
        == 404
    )
