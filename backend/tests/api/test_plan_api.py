"""M-08 plan API: generate/persist/auto-start + owner-safe summary query."""

from __future__ import annotations

import pytest
from app.agents.plan_service import PlanGenerationService
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from app.plan.nodes import NodeRegistry
from app.providers.inference import InferenceResult, ModelInferenceClient
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _FakePlanInference(ModelInferenceClient):
    async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
        return InferenceResult(
            text=(
                '{"schema_version":"m08.1","task_id":1,"spec_version":1,'
                '"task_type":"SPECIFIED_SOURCE",'
                '"nodes":['
                '{"node_id":"n1","node_type":"fetch","definition_version":"1.0.0",'
                '"parameters":{"url_template":"https://example.com/item/{id}"},"depends_on":[],"optional":false,"fail_policy":"block"},'
                '{"node_id":"n2","node_type":"extract","definition_version":"1.0.0",'
                '"parameters":{"fields":["公司名"]},"depends_on":["n1"],"optional":false,"fail_policy":"block"}'
                "],"
                '"edges":[],"reasoning_summary":"逐页抓取并抽取"}'
            ),
            provider_type="openai",
            duration_ms=1,
        )


@pytest.fixture()
def plan_client(tmp_path) -> dict:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'plan.db'}", connect_args={"check_same_thread": False}
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

    def _override_generation():
        return PlanGenerationService(
            provider_service=None,
            vault=None,
            registry=NodeRegistry(),
            inference=_FakePlanInference(),
        )

    from app.api.routes.plans import get_plan_generation_service

    app.dependency_overrides[get_plan_generation_service] = _override_generation
    with TestClient(app) as client:
        yield {"client": client, "factory": factory}
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _confirmed_spec(client: TestClient, task_id: int) -> int:
    spec = SpecDraftPayload(
        task_type=TaskType.SPECIFIED_SOURCE,
        goal="抓取指定网站公司信息",
        fields=[{"name": "公司名", "type": "text", "required": True}],
        source_scope={
            "mode": "SPECIFIED_SOURCE",
            "seed_urls": ["https://example.com"],
            "source_hints": [],
        },
    )
    r = client.post(
        f"/api/tasks/{task_id}/spec-confirm",
        json={"expected_version": 1, "payload": spec.model_dump(mode="json")},
    )
    assert r.status_code == 200, r.text
    return r.json()["spec_version"]


def test_plan_generate_persists_and_returns_summary(plan_client: dict) -> None:
    c = plan_client["client"]
    _register(c, "alice@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)

    r = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_version"] == 1
    assert body["validation_status"] in ("VALID", "REQUIRES_APPROVAL")

    summary = c.get(f"/api/tasks/{task_id}/plans/1")
    assert summary.status_code == 200
    assert summary.json()["node_count"] == 2
    assert summary.json()["validation_status"] == body["validation_status"]


def test_plan_rejects_missing_spec_owner_safe(plan_client: dict) -> None:
    """不存在的 spec_version → owner-safe 404（不泄漏资源存在性）。"""
    c = plan_client["client"]
    _register(c, "alice@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    r = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": 99, "expected_version": 1},
    )
    assert r.status_code == 404, r.text


def test_plan_owner_isolation(plan_client: dict) -> None:
    c = plan_client["client"]
    _register(c, "alice@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)
    c.post(f"/api/tasks/{task_id}/plan", json={"spec_version": spec_version, "expected_version": 2})

    _register(c, "bob@example.com")
    assert c.get(f"/api/tasks/{task_id}/plans/1").status_code == 404
