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
from app.plan.preflight import (
    ExecutionPreflightOutcome,
    ExecutionPreflightService,
    ExecutionPreflightStatus,
)
from app.plan.preflight_repository import ExecutionPreflightRepository
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


def test_hybrid_freezes_newest_available_search_config(hybrid_preflight_case):
    """当存在多个可用 SearchConfig 时，冻结执行默认选择最新配置（未来任务默认切换）。"""
    from datetime import UTC, datetime, timedelta

    db = hybrid_preflight_case.db
    repo = SearchConfigRepository(db)
    user_id = hybrid_preflight_case.user.id
    older = repo.create_version(
        user_id=user_id,
        name="older",
        provider_type="tavily",
        base_url=None,
        credential_version_id=None,
    )
    newer = repo.create_version(
        user_id=user_id,
        name="newer",
        provider_type="bocha",
        base_url=None,
        credential_version_id=None,
    )
    older.connection_status = "available"
    newer.connection_status = "available"
    now = datetime.now(UTC)
    older.created_at = now - timedelta(days=1)
    newer.created_at = now
    db.commit()
    outcome = hybrid_preflight_case.service.evaluate(
        user_id=user_id,
        task_id=hybrid_preflight_case.task.id,
        spec_version=hybrid_preflight_case.spec.version,
        plan_version=hybrid_preflight_case.plan.version,
    )
    assert outcome.status is ExecutionPreflightStatus.READY
    assert outcome.search_config_id == newer.config_id
    assert outcome.search_config_version == newer.version


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


def test_reused_preflight_returns_the_immutable_persisted_winner(preflight_case):
    kwargs = {
        "user_id": preflight_case.user.id,
        "task_id": preflight_case.task.id,
        "spec_version": preflight_case.spec.version,
        "plan_version": preflight_case.plan.version,
    }
    blocked_settings = preflight_case.settings.model_copy(update={"s3_bucket": ""})
    first = ExecutionPreflightService(preflight_case.db, settings=blocked_settings).evaluate(
        **kwargs
    )
    second = ExecutionPreflightService(
        preflight_case.db, settings=preflight_case.settings
    ).evaluate(**kwargs)
    assert first.status is ExecutionPreflightStatus.BLOCKED
    assert second.created is False
    assert second.result_id == first.result_id
    assert second.status is first.status
    assert second.issue_codes == first.issue_codes
    assert second.search_config_id == first.search_config_id
    assert second.search_config_version == first.search_config_version
    assert second.capability_manifest_version == first.capability_manifest_version


@pytest.mark.parametrize(
    ("model_config_id", "model_config_version"),
    [("frozen-model", None), (None, 1)],
)
def test_half_populated_frozen_model_identity_is_blocked(
    preflight_case, model_config_id, model_config_version
):
    case = _case(preflight_case.db)
    case.plan.model_config_id = model_config_id
    case.plan.model_config_version = model_config_version
    case.db.commit()
    outcome = case.service.evaluate(
        user_id=case.user.id,
        task_id=case.task.id,
        spec_version=case.spec.version,
        plan_version=case.plan.version,
    )
    assert "FROZEN_CONFIG_UNAVAILABLE" in outcome.issue_codes


def test_preflight_insert_does_not_commit_unrelated_pending_work(preflight_case):
    pending = Task(
        user_id=preflight_case.user.id,
        title="must remain uncommitted",
        task_type=TaskType.SPECIFIED_SOURCE.value,
    )
    preflight_case.db.add(pending)
    outcome = preflight_case.service.evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert outcome.created is True
    assert pending.id is not None

    other = Session(bind=preflight_case.db.get_bind(), expire_on_commit=False)
    try:
        assert other.get(Task, pending.id) is None
    finally:
        other.close()
        preflight_case.db.rollback()


def test_integrity_recovery_reloads_winner_and_keeps_session_usable(preflight_case, monkeypatch):
    outcome = ExecutionPreflightOutcome(
        status=ExecutionPreflightStatus.READY,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
        capability_manifest_version="controlled-race-v1",
        issues=[],
    )
    other = Session(bind=preflight_case.db.get_bind(), expire_on_commit=False)
    try:
        winner = ExecutionPreflightResult(
            task_id=outcome.task_id,
            user_id=preflight_case.user.id,
            spec_version=outcome.spec_version,
            plan_version=outcome.plan_version,
            capability_manifest_version=outcome.capability_manifest_version,
            status=outcome.status.value,
            issues=[],
        )
        other.add(winner)
        other.commit()

        repository = ExecutionPreflightRepository(preflight_case.db)
        original_find = repository._find_existing
        calls = 0

        def first_lookup_misses(candidate):
            nonlocal calls
            calls += 1
            return None if calls == 1 else original_find(candidate)

        monkeypatch.setattr(repository, "_find_existing", first_lookup_misses)
        recovered, created = repository.get_or_create(outcome)

        assert created is False
        assert recovered.id == winner.id
        assert preflight_case.db.scalar(select(Task.id).where(Task.id == preflight_case.task.id))
        preflight_case.db.add(
            Task(
                user_id=preflight_case.user.id,
                title="session remains usable",
                task_type=TaskType.SPECIFIED_SOURCE.value,
            )
        )
        preflight_case.db.flush()
    finally:
        preflight_case.db.rollback()
        other.close()


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


def test_replan_preflight_carries_frozen_config_to_continuation_plan(hybrid_preflight_case):
    """Round-2 Replan：新 plan_version 的 preflight 继承 Run 冻结配置，不切到当前 default。

    Task freezes SearchConfig v1 → 稍后 v2 成为 current/default → Round-2 Replan
    仍冻结 v1。若 replan 不携带冻结配置，Round-2 source_search 会因缺少 READY
    preflight 抛 FROZEN_CONFIG_UNAVAILABLE（Task 130 真实故障）。
    """
    from app.activities.replan import _ensure_continuation_preflight

    case = hybrid_preflight_case
    db = case.db
    frozen = case.search  # v1, available
    db.add(
        ExecutionPreflightResult(
            user_id=case.user.id,
            task_id=case.task.id,
            spec_version=case.spec.version,
            plan_version=case.plan.version,
            capability_manifest_version="test-manifest",
            status="READY",
            issues=[],
            search_config_id=frozen.config_id,
            search_config_version=frozen.version,
        )
    )
    # v2 成为 current/default（get_first_available 会选它；冻结必须仍用 v1）
    current = SearchConfigRepository(db).append_version(
        config_id=frozen.config_id,
        user_id=case.user.id,
        name="current v2",
        provider_type="bocha",
        base_url=None,
        credential_version_id=None,
    )
    current.connection_status = "available"
    v2 = PlanVersionRepository(db).create(
        user_id=case.user.id,
        task_id=case.task.id,
        spec_version=case.spec.version,
        version=2,
        payload=_payload(
            task_id=case.task.id, spec_version=case.spec.version, task_type=TaskType.HYBRID
        ),
        validation_status="VALID",
        plan_fingerprint="b" * 64,
        registry_versions={},
        generation_policy="replan",
        trigger_reason="continuation_search_more_required",
        parent_plan_version_id=case.plan.id,
        commit=False,
    )
    case.task.current_plan_version = v2.version
    db.commit()

    ready = _ensure_continuation_preflight(
        db,
        user_id=case.user.id,
        task_id=case.task.id,
        spec_version=case.spec.version,
        new_plan_version=v2.version,
    )
    db.commit()

    assert ready is True
    row = db.scalar(
        select(ExecutionPreflightResult).where(
            ExecutionPreflightResult.task_id == case.task.id,
            ExecutionPreflightResult.plan_version == v2.version,
        )
    )
    assert row is not None
    assert row.status == ExecutionPreflightStatus.READY.value
    assert row.search_config_id == frozen.config_id
    assert row.search_config_version == frozen.version  # 冻结 v1，而非 current v2


def test_replan_preflight_revoked_frozen_config_blocks_without_fallback(
    hybrid_preflight_case,
):
    """冻结配置被 revoke：显式 BLOCKED（FROZEN_CONFIG_UNAVAILABLE），不回退到 current default。"""
    from app.activities.replan import _ensure_continuation_preflight

    case = hybrid_preflight_case
    db = case.db
    frozen = case.search
    db.add(
        ExecutionPreflightResult(
            user_id=case.user.id,
            task_id=case.task.id,
            spec_version=case.spec.version,
            plan_version=case.plan.version,
            capability_manifest_version="test-manifest",
            status="READY",
            issues=[],
            search_config_id=frozen.config_id,
            search_config_version=frozen.version,
        )
    )
    # 新的 current/default 可用配置（不得被静默选中）
    current = SearchConfigRepository(db).append_version(
        config_id=frozen.config_id,
        user_id=case.user.id,
        name="current v2",
        provider_type="bocha",
        base_url=None,
        credential_version_id=None,
    )
    current.connection_status = "available"
    # revoke 冻结配置行
    db.query(SearchConfig).filter(
        SearchConfig.config_id == frozen.config_id,
        SearchConfig.version == frozen.version,
    ).delete()
    v2 = PlanVersionRepository(db).create(
        user_id=case.user.id,
        task_id=case.task.id,
        spec_version=case.spec.version,
        version=2,
        payload=_payload(
            task_id=case.task.id, spec_version=case.spec.version, task_type=TaskType.HYBRID
        ),
        validation_status="VALID",
        plan_fingerprint="b" * 64,
        registry_versions={},
        generation_policy="replan",
        trigger_reason="continuation_search_more_required",
        parent_plan_version_id=case.plan.id,
        commit=False,
    )
    case.task.current_plan_version = v2.version
    db.commit()

    ready = _ensure_continuation_preflight(
        db,
        user_id=case.user.id,
        task_id=case.task.id,
        spec_version=case.spec.version,
        new_plan_version=v2.version,
    )
    db.commit()

    assert ready is False
    row = db.scalar(
        select(ExecutionPreflightResult).where(
            ExecutionPreflightResult.task_id == case.task.id,
            ExecutionPreflightResult.plan_version == v2.version,
        )
    )
    assert row is not None
    assert row.status == ExecutionPreflightStatus.BLOCKED.value
    assert any(issue["code"] == "FROZEN_CONFIG_UNAVAILABLE" for issue in row.issues)
    assert row.search_config_id is None  # 不回退到 current v2
