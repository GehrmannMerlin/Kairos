"""M-08 Task 4: immutable plan version + replan v2 + deterministic diff."""

from __future__ import annotations

from app.domain.idempotency import stable_fingerprint
from app.domain.task_types import TaskType
from app.plan.diff import PlanDiff
from app.plan.nodes import NodeType
from app.plan.schemas import PlanGraphDraft, PlanValidationResult
from app.plan.validator import validate_plan
from tests.plan.test_plan_fixtures import _SPEC, _node  # noqa: F401


def _v1() -> PlanGraphDraft:
    return PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.SPECIFIED_SOURCE,
        nodes=[
            _node("n1", NodeType.FETCH, parameters={"url_template": "https://example.com/{id}"}),
            _node("n2", NodeType.EXTRACT, parameters={"fields": ["公司名"]}),
        ],
    )


def _v2() -> PlanGraphDraft:
    return PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.SPECIFIED_SOURCE,
        nodes=[
            _node("n1", NodeType.FETCH, parameters={"url_template": "https://example.com/{id}"}),
            _node("n3", NodeType.NORMALIZE),
            _node("n2", NodeType.EXTRACT, parameters={"fields": ["公司名"]}),
        ],
    )


def test_diff_detects_added_and_unchanged() -> None:
    diff = PlanDiff.compute(_v1(), _v2())
    assert "n3" in diff.added_nodes
    assert diff.removed_nodes == []
    assert diff.changed_parameters == {}
    assert diff.impact_scope == "execution_strategy"


def test_diff_detects_parameter_change() -> None:
    after = _v2()
    after.nodes[0].parameters["url_template"] = "https://other.com/{id}"
    diff = PlanDiff.compute(_v1(), after)
    assert diff.changed_parameters == {"n1": {"url_template": "https://other.com/{id}"}}


def test_plan_fingerprint_is_stable_and_versioned() -> None:
    f1 = stable_fingerprint("plan", _v1().model_dump(mode="json"), {"fetch": "1.0.0"})
    f2 = stable_fingerprint("plan", _v2().model_dump(mode="json"), {"fetch": "1.0.0"})
    assert f1 != f2
    assert len(f1) == 64


def test_replan_that_changes_spec_boundary_is_rejected() -> None:
    # replan 把 fetch 指向范围外域名 → 必须 REQUIRES_NEW_SPEC，不能仅 Approval 放行
    v2 = _v2()
    v2.nodes[0].parameters["url_template"] = "https://other-domain.com/{id}"
    outcome = validate_plan(v2, _SPEC)
    assert outcome.result == PlanValidationResult.REQUIRES_NEW_SPEC


def test_replan_persists_v2_with_parent_and_v1_immutable(tmp_path) -> None:
    """PlanService.create_replan 产生 v2（parent=v1），v1 不被修改。"""
    from app.domain.models import PlanVersion
    from app.infra.db import Base
    from app.plan.service import PlanService, plan_fingerprint
    from app.workflows.starter import TaskWorkflowStarter
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        f"sqlite:///{tmp_path / 'replan.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    class _StubStarter(TaskWorkflowStarter):
        def __init__(self) -> None:  # 不连接 Temporal
            pass

        async def submit_validated_plan(self, **kw):
            from app.workflows.starter import RunStartedResult

            return RunStartedResult(run_id=0, workflow_id="")

    from app.domain.repository import TaskRepository

    TaskRepository(session).create(user_id=1, title="replan task")

    svc = PlanService(session, starter=_StubStarter())
    registry_versions = {"fetch": "1.0.0", "extract": "1.0.0", "normalize": "1.0.0"}
    v1 = svc.persist_plan(
        user_id=1,
        task_id=1,
        spec_version=1,
        graph=_v1().model_dump(mode="json"),
        validation_status="VALID",
        fingerprint_value=plan_fingerprint(_v1().model_dump(mode="json"), registry_versions),
        registry_versions=registry_versions,
    )
    v2 = svc.create_replan(
        user_id=1,
        task_id=1,
        spec_version=1,
        graph=_v2().model_dump(mode="json"),
        fingerprint_value=plan_fingerprint(_v2().model_dump(mode="json"), registry_versions),
        registry_versions=registry_versions,
        trigger_reason="来源失效",
        replan_evidence_refs=["evidence:1"],
        diff_summary={"added_nodes": ["n3"]},
    )
    assert v1.version == 1
    assert v2.version == 2
    assert v2.parent_plan_version_id == v1.id
    assert v2.generation_policy == "replan"
    assert v2.trigger_reason == "来源失效"

    # v1 不可变：仍指向原始 graph，diff 未写入 v1
    v1_reload = session.get(PlanVersion, v1.id)
    assert v1_reload.payload["graph"]["nodes"][0]["node_type"] == "fetch"
    assert v1_reload.diff_summary is None
    assert v1_reload.generation_policy == "auto"
    session.close()
