"""M-08 plan API: generate/persist/auto-start + owner-safe summary query."""

from __future__ import annotations

import asyncio

import pytest
from app.agents.plan_service import PlanGenerationService
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.config import Settings, get_settings
from app.domain.models import DomainEvent, PlanVersion, Run
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
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode


class _FakePlanInference(ModelInferenceClient):
    def __init__(self, state: dict) -> None:
        # The fake supplies generate() completely and intentionally has no transport state.
        self._state = state

    async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
        if self._state.get("generation_delay"):
            await asyncio.sleep(self._state["generation_delay"])
        return InferenceResult(
            text=(
                '{"schema_version":"m08.1","task_id":999,"spec_version":999,'
                '"task_type":"EXPLORATORY",'
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


class _FakeTemporalClient:
    def __init__(self, state: dict) -> None:
        self._state = state
        self.started_ids: set[str] = set()
        self.calls: list[dict] = []

    async def start_workflow(self, *args, **kwargs):
        self.calls.append(kwargs)
        mode = self._state.get("temporal_mode", "success")
        if mode == "slow":
            await asyncio.sleep(self._state.get("temporal_delay", 0.05))
        if mode == "rpc_error":
            raise RPCError("temporal unavailable", RPCStatusCode.UNAVAILABLE, b"")
        if mode == "type_error":
            raise TypeError("programming bug")
        workflow_id = kwargs["id"]
        if workflow_id in self.started_ids:
            raise WorkflowAlreadyStartedError(workflow_id, "task_workflow")
        self.started_ids.add(workflow_id)


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
    state = {
        "temporal_mode": "success",
        "plan_lifecycle_timeout_seconds": 105.0,
        "s3_endpoint": "localhost:9000",
    }
    temporal = _FakeTemporalClient(state)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    app.dependency_overrides[get_settings] = lambda: Settings(
        plan_lifecycle_timeout_seconds=state["plan_lifecycle_timeout_seconds"],
        s3_endpoint=state["s3_endpoint"],
    )

    def _override_generation():
        return PlanGenerationService(
            provider_service=None,
            vault=None,
            registry=NodeRegistry(),
            inference=_FakePlanInference(state),
        )

    from app.api.routes.plans import (
        get_plan_generation_service,
        get_temporal_client_factory,
    )

    async def _client_factory():
        if state.get("temporal_mode") == "factory_rpc_error":
            raise RPCError("temporal unavailable", RPCStatusCode.UNAVAILABLE, b"")
        return temporal

    app.dependency_overrides[get_plan_generation_service] = _override_generation
    app.dependency_overrides[get_temporal_client_factory] = lambda: _client_factory
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"client": client, "factory": factory, "state": state, "temporal": temporal}
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


def test_plan_generate_persists_and_returns_summary(plan_client: dict, caplog) -> None:
    caplog.set_level("INFO", logger="kairos.inference_lifecycle")
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
    assert body["run_state"] == "pending"
    assert body["start_recoverable"] is False
    assert body["preflight_status"] == "READY"
    assert body["preflight_issues"] == []
    lifecycle_events = [
        record.event_name for record in caplog.records if hasattr(record, "event_name")
    ]
    assert "plan.validation_finished" in lifecycle_events
    assert "plan.persisted" in lifecycle_events
    assert "plan.workflow_start_finished" in lifecycle_events

    summary = c.get(f"/api/tasks/{task_id}/plans/1")
    assert summary.status_code == 200
    assert summary.json()["node_count"] == 2
    assert summary.json()["validation_status"] == body["validation_status"]
    assert summary.json()["preflight_status"] == "READY"
    with plan_client["factory"]() as session:
        assert (
            session.query(DomainEvent)
            .filter(DomainEvent.aggregate_id == task_id)
            .filter(DomainEvent.event_type == "task.execution_preflight_ready")
            .count()
            == 1
        )


def test_plan_persists_but_does_not_start_when_preflight_blocks(plan_client: dict) -> None:
    """A missing execution dependency blocks before a Run or Temporal RPC exists."""
    c = plan_client["client"]
    _register(c, "preflight-blocked@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)

    plan_client["state"]["s3_endpoint"] = ""

    response = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "EXECUTION_PREFLIGHT_BLOCKED"
    assert detail["preflight_status"] == "BLOCKED"
    assert detail["preflight_issues"][0]["code"] == "ARTIFACT_STORAGE_UNAVAILABLE"
    assert plan_client["temporal"].calls == []

    retry = c.post(f"/api/tasks/{task_id}/plans/1/start")
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "EXECUTION_PREFLIGHT_BLOCKED"
    with plan_client["factory"]() as session:
        assert session.query(PlanVersion).filter_by(task_id=task_id).count() == 1
        assert session.query(Run).filter_by(task_id=task_id).count() == 0
        events = (
            session.query(DomainEvent)
            .filter(DomainEvent.aggregate_id == task_id)
            .filter(DomainEvent.event_type == "task.execution_preflight_blocked")
            .all()
        )
        assert len(events) == 1


def test_plan_persists_command_owned_identity_over_model_identity(plan_client: dict) -> None:
    c = plan_client["client"]
    _register(c, "identity@example.com")
    c.post("/api/tasks", json={"content": "占用模型示例任务 ID"})
    task_id = c.post("/api/tasks", json={"content": "抓取指定网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)

    response = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )

    assert response.status_code == 200, response.text
    session = plan_client["factory"]()
    try:
        row = session.query(PlanVersion).filter_by(task_id=task_id, version=1).one()
    finally:
        session.close()
    graph = row.payload["graph"]
    assert graph["task_id"] == task_id
    assert graph["spec_version"] == spec_version
    assert graph["task_type"] == TaskType.SPECIFIED_SOURCE.value


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
    assert c.post(f"/api/tasks/{task_id}/plans/1/start").status_code == 404


def test_temporal_failure_persists_one_plan_and_pending_run(plan_client: dict) -> None:
    c = plan_client["client"]
    state = plan_client["state"]
    _register(c, "start-fail@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)
    state["temporal_mode"] = "factory_rpc_error"

    response = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "PLAN_START_FAILED"
    assert detail["plan_version"] == 1
    assert detail["start_recoverable"] is True
    with plan_client["factory"]() as session:
        assert session.query(PlanVersion).filter_by(task_id=task_id).count() == 1
        runs = session.query(Run).filter_by(task_id=task_id).all()
        assert len(runs) == 1
        assert runs[0].state == "pending"
        assert detail["run_id"] == runs[0].id


def test_start_only_retry_reuses_pending_run(plan_client: dict) -> None:
    c = plan_client["client"]
    state = plan_client["state"]
    _register(c, "start-retry@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)
    state["temporal_mode"] = "factory_rpc_error"
    failed = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )
    run_id = failed.json()["detail"]["run_id"]

    state["temporal_mode"] = "success"
    first = c.post(f"/api/tasks/{task_id}/plans/1/start")
    second = c.post(f"/api/tasks/{task_id}/plans/1/start")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["run_id"] == run_id
    assert second.json()["run_id"] == run_id
    assert first.json()["workflow_id"] == f"task-workflow-{task_id}"
    assert first.json()["preflight_status"] == "READY"
    with plan_client["factory"]() as session:
        assert session.query(Run).filter_by(task_id=task_id).count() == 1


def test_plan_lifecycle_timeout_does_not_duplicate_persistence(plan_client: dict) -> None:
    c = plan_client["client"]
    state = plan_client["state"]
    _register(c, "lifecycle-timeout@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)
    state["plan_lifecycle_timeout_seconds"] = 0.1
    state["temporal_mode"] = "slow"
    state["temporal_delay"] = 0.3

    first = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )
    retry = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )

    assert first.status_code == 504
    assert first.json()["detail"]["code"] == "PLAN_GENERATION_TIMEOUT"
    assert retry.status_code == 409
    with plan_client["factory"]() as session:
        assert session.query(PlanVersion).filter_by(task_id=task_id).count() == 1
        assert session.query(Run).filter_by(task_id=task_id).count() == 1


def test_unexpected_start_programming_error_remains_http_500(plan_client: dict) -> None:
    c = plan_client["client"]
    state = plan_client["state"]
    _register(c, "start-type-error@example.com")
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    spec_version = _confirmed_spec(c, task_id)
    state["temporal_mode"] = "type_error"

    response = c.post(
        f"/api/tasks/{task_id}/plan",
        json={"spec_version": spec_version, "expected_version": 2},
    )

    assert response.status_code == 500
    assert "PLAN_START_FAILED" not in response.text
    assert "PROVIDER_TIMEOUT" not in response.text
