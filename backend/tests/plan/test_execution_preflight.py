"""Persisted, owner-scoped execution preflight contracts."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from app.auth.errors import NotFoundError
from app.auth.models import User
from app.auth.repository import UserRepository
from app.config import Settings
from app.credentials.models import SearchConfig
from app.domain.models import CollectionSpecVersion, ExecutionPreflightResult, PlanVersion, Task
from app.domain.repository import PlanVersionRepository, SpecVersionRepository, TaskRepository
from app.domain.task_types import TaskType
from app.plan.nodes import NodeType
from app.plan.preflight import ExecutionPreflightService, ExecutionPreflightStatus
from app.providers.repository import SearchConfigRepository
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class PreflightCase:
    db: Session
    user: User
    task: Task
    spec: CollectionSpecVersion
    plan: PlanVersion
    settings: Settings
    service: ExecutionPreflightService
    search: SearchConfig | None = None


def _payload(*, task_id: int, spec_version: int, task_type: TaskType) -> dict:
    nodes = [
        {
            "node_id": "fetch-1",
            "node_type": NodeType.FETCH.value,
            "definition_version": "1.0.0",
            "parameters": {"url_template": "https://example.com/{id}"},
            "depends_on": [],
        }
    ]
    if task_type is TaskType.HYBRID:
        nodes.insert(
            0,
            {
                "node_id": "search-1",
                "node_type": NodeType.SOURCE_SEARCH.value,
                "definition_version": "1.0.0",
                "parameters": {"query": "公司信息"},
                "depends_on": [],
            },
        )
    return {
        "graph": {
            "task_id": task_id,
            "spec_version": spec_version,
            "task_type": task_type.value,
            "nodes": nodes,
            "edges": [],
        }
    }


def _spec_payload(*, task_type: TaskType, seed_urls: list[str]) -> dict:
    return {
        "task_type": task_type.value,
        "goal": "采集公司信息",
        "fields": [{"name": "公司名", "type": "text", "required": True}],
        "source_scope": {
            "mode": task_type.value,
            "seed_urls": seed_urls,
            "source_hints": [],
        },
    }


def _case(
    db: Session,
    *,
    task_type: TaskType = TaskType.SPECIFIED_SOURCE,
    seed_urls: list[str] | None = None,
    settings: Settings | None = None,
) -> PreflightCase:
    user_count = db.scalar(select(func.count(User.id))) or 0
    user = UserRepository(db).create(f"preflight-{user_count}@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=user.id, title="preflight", task_type=task_type.value)
    spec = SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=_spec_payload(
            task_type=task_type,
            seed_urls=seed_urls if seed_urls is not None else ["https://example.com"],
        ),
    )
    plan = PlanVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        spec_version=spec.version,
        version=1,
        payload=_payload(task_id=task.id, spec_version=spec.version, task_type=task_type),
        validation_status="VALID",
        plan_fingerprint="a" * 64,
        registry_versions={},
    )
    task.current_spec_version = spec.version
    task.current_plan_version = plan.version
    db.commit()
    resolved_settings = settings or Settings()
    return PreflightCase(
        db=db,
        user=user,
        task=task,
        spec=spec,
        plan=plan,
        settings=resolved_settings,
        service=ExecutionPreflightService(db, settings=resolved_settings),
    )


@pytest.fixture()
def preflight_case(tmp_path) -> Iterator[PreflightCase]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preflight.db'}", connect_args={"check_same_thread": False}
    )
    from app.infra.db import Base

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield _case(session)
    finally:
        session.close()


@pytest.fixture()
def hybrid_preflight_case(tmp_path) -> Iterator[PreflightCase]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'hybrid-preflight.db'}", connect_args={"check_same_thread": False}
    )
    from app.infra.db import Base

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        case = _case(session, task_type=TaskType.HYBRID)
        search = SearchConfigRepository(session).create_version(
            user_id=case.user.id,
            name="available search",
            provider_type="tavily",
            base_url=None,
            credential_version_id=None,
        )
        search.connection_status = "available"
        session.commit()
        case.search = search
        yield case
    finally:
        session.close()


def test_empty_specified_seed_is_blocked(preflight_case):
    preflight_case = _case(preflight_case.db, seed_urls=[])
    outcome = preflight_case.service.evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert outcome.status is ExecutionPreflightStatus.BLOCKED
    assert outcome.issue_codes == ("EXECUTION_INPUT_UNMATERIALIZABLE",)


def test_hybrid_freezes_available_search_config(hybrid_preflight_case):
    outcome = hybrid_preflight_case.service.evaluate(
        user_id=hybrid_preflight_case.user.id,
        task_id=hybrid_preflight_case.task.id,
        spec_version=hybrid_preflight_case.spec.version,
        plan_version=hybrid_preflight_case.plan.version,
    )
    assert outcome.status is ExecutionPreflightStatus.READY
    assert hybrid_preflight_case.search is not None
    assert outcome.search_config_id == hybrid_preflight_case.search.config_id
    assert outcome.search_config_version == hybrid_preflight_case.search.version


def test_unsupported_node_is_blocked(preflight_case):
    service = ExecutionPreflightService(
        preflight_case.db,
        settings=preflight_case.settings,
        supported_nodes={NodeType.FETCH},
    )
    preflight_case.plan.payload["graph"]["nodes"].append(
        {
            "node_id": "extract-1",
            "node_type": NodeType.EXTRACT.value,
            "definition_version": "1.0.0",
            "parameters": {"fields": ["公司名"]},
            "depends_on": ["fetch-1"],
        }
    )
    preflight_case.db.commit()
    outcome = service.evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert "EXECUTION_CAPABILITY_UNAVAILABLE" in outcome.issue_codes


def test_preflight_is_idempotent_per_frozen_versions_and_manifest(preflight_case):
    kwargs = {
        "user_id": preflight_case.user.id,
        "task_id": preflight_case.task.id,
        "spec_version": preflight_case.spec.version,
        "plan_version": preflight_case.plan.version,
    }
    first = preflight_case.service.evaluate(**kwargs)
    second = preflight_case.service.evaluate(**kwargs)
    assert first.result_id == second.result_id
    assert first.created is True
    assert second.created is False
    assert preflight_case.db.scalar(select(func.count(ExecutionPreflightResult.id))) == 1


def test_unavailable_queue_route_is_blocked(preflight_case):
    settings = preflight_case.settings.model_copy(update={"worker_roles": "core"})
    outcome = ExecutionPreflightService(preflight_case.db, settings=settings).evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert "TASK_QUEUE_ROUTE_UNAVAILABLE" in outcome.issue_codes


def test_unavailable_frozen_model_config_is_blocked(preflight_case):
    preflight_case.plan.model_config_id = "missing-model-config"
    preflight_case.plan.model_config_version = 1
    preflight_case.db.commit()
    outcome = preflight_case.service.evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert "FROZEN_CONFIG_UNAVAILABLE" in outcome.issue_codes


def test_unavailable_artifact_storage_is_blocked(preflight_case):
    settings = preflight_case.settings.model_copy(
        update={"s3_endpoint": "", "s3_bucket": "", "s3_access_key": ""}
    )
    outcome = ExecutionPreflightService(preflight_case.db, settings=settings).evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert "ARTIFACT_STORAGE_UNAVAILABLE" in outcome.issue_codes


def test_plan_context_mismatch_is_blocked(preflight_case):
    preflight_case.plan.payload["graph"]["task_id"] = preflight_case.task.id + 1
    preflight_case.db.commit()
    outcome = preflight_case.service.evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert "PLAN_CONTEXT_MISMATCH" in outcome.issue_codes


def test_non_owner_cannot_evaluate_preflight(preflight_case):
    other = UserRepository(preflight_case.db).create("other@example.com", "hash", None)
    with pytest.raises(NotFoundError):
        preflight_case.service.evaluate(
            user_id=other.id,
            task_id=preflight_case.task.id,
            spec_version=preflight_case.spec.version,
            plan_version=preflight_case.plan.version,
        )
