"""Dedupe invariant（D-014）：record-producing 计划必须经过 Normalize → Deduplicate → Validate。"""

from __future__ import annotations

import pytest
from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft, PlanNodeInstance, PlanValidationResult
from app.plan.validator import validate_plan


def _node(
    node_id: str, node_type: str, *, parameters: dict, depends_on: list[str] | None = None
) -> PlanNodeInstance:
    return PlanNodeInstance(
        node_id=node_id,
        node_type=node_type,
        definition_version="1.0.0",
        parameters=parameters,
        depends_on=depends_on or [],
    )


def _spec(*, task_type: TaskType, seed_urls: list[str]) -> dict:
    return SpecDraftPayload(
        task_type=task_type,
        goal="采集公司信息",
        fields=[{"name": "公司名", "type": "text", "required": True}],
        source_scope={
            "mode": task_type,
            "seed_urls": seed_urls,
            "source_hints": [],
        },
    ).model_dump(mode="json")


def _record_chain(*, with_normalize: bool = True, with_deduplicate: bool = True) -> PlanGraphDraft:
    nodes = [
        _node("access", "access_rules_check", parameters={}),
        _node("link", "link_discovery", parameters={}, depends_on=["access"]),
        _node(
            "fetch",
            "fetch",
            parameters={"url_template": "https://example.com"},
            depends_on=["link"],
        ),
        _node("extract", "extract", parameters={"fields": ["公司名"]}, depends_on=["fetch"]),
    ]
    prev = "extract"
    if with_normalize:
        nodes.append(_node("normalize", "normalize", parameters={}, depends_on=[prev]))
        prev = "normalize"
    if with_deduplicate:
        nodes.append(_node("dedupe", "deduplicate", parameters={}, depends_on=[prev]))
        prev = "dedupe"
    nodes.append(_node("validate", "validate", parameters={}, depends_on=[prev]))
    nodes.append(_node("artifact", "generate_artifact", parameters={}, depends_on=["validate"]))
    return PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.SPECIFIED_SOURCE,
        nodes=nodes,
    )


def test_record_chain_without_deduplicate_is_invalid() -> None:
    outcome = validate_plan(
        _record_chain(with_deduplicate=False),
        _spec(task_type=TaskType.SPECIFIED_SOURCE, seed_urls=["https://example.com"]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.INVALID
    codes = {issue.code for issue in outcome.issues}
    assert "REQUIRED_CAPABILITY_MISSING" in codes


def test_record_chain_without_normalize_is_invalid() -> None:
    outcome = validate_plan(
        _record_chain(with_normalize=False),
        _spec(task_type=TaskType.SPECIFIED_SOURCE, seed_urls=["https://example.com"]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.INVALID
    codes = {issue.code for issue in outcome.issues}
    assert "REQUIRED_CAPABILITY_MISSING" in codes


def test_record_chain_with_normalize_and_deduplicate_is_valid() -> None:
    outcome = validate_plan(
        _record_chain(),
        _spec(task_type=TaskType.SPECIFIED_SOURCE, seed_urls=["https://example.com"]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.VALID


def test_hybrid_record_chain_missing_deduplicate_is_invalid() -> None:
    # HYBRID 先 source_search 再进入流水线；缺 deduplicate 仍触发 REQUIRED_CAPABILITY_MISSING。
    graph = PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.HYBRID,
        nodes=[
            _node("source", "source_search", parameters={"query": "公司信息"}),
            _node("access", "access_rules_check", parameters={}, depends_on=["source"]),
            _node("link", "link_discovery", parameters={}, depends_on=["access"]),
            _node(
                "fetch",
                "fetch",
                parameters={"url_template": "https://example.com/{id}"},
                depends_on=["link"],
            ),
            _node("extract", "extract", parameters={"fields": ["公司名"]}, depends_on=["fetch"]),
            _node("normalize", "normalize", parameters={}, depends_on=["extract"]),
            _node("validate", "validate", parameters={}, depends_on=["normalize"]),
            _node("artifact", "generate_artifact", parameters={}, depends_on=["validate"]),
        ],
    )
    outcome = validate_plan(
        graph,
        _spec(task_type=TaskType.HYBRID, seed_urls=[]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.INVALID
    assert "REQUIRED_CAPABILITY_MISSING" in {issue.code for issue in outcome.issues}


def test_pure_discovery_plan_does_not_require_deduplicate() -> None:
    graph = PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.EXPLORATORY,
        nodes=[
            _node("source", "source_search", parameters={"query": "公司信息"}),
        ],
    )
    outcome = validate_plan(
        graph,
        _spec(task_type=TaskType.EXPLORATORY, seed_urls=[]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.VALID
    assert "REQUIRED_CAPABILITY_MISSING" not in {issue.code for issue in outcome.issues}


@pytest.mark.parametrize("task_type", [TaskType.SPECIFIED_SOURCE, TaskType.HYBRID])
def test_snapshot_only_plan_does_not_require_deduplicate(task_type: TaskType) -> None:
    # 无 VALIDATE / GENERATE_ARTIFACT 的计划不产生正式 Record，不强制 deduplicate。
    nodes = [
        _node("fetch", "fetch", parameters={"url_template": "https://example.com"}),
        _node("extract", "extract", parameters={"fields": ["公司名"]}, depends_on=["fetch"]),
    ]
    seed_urls = ["https://example.com"] if task_type is TaskType.SPECIFIED_SOURCE else []
    graph = PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=task_type,
        nodes=nodes,
    )
    outcome = validate_plan(
        graph,
        _spec(task_type=task_type, seed_urls=seed_urls),
        NodeRegistry(),
    )
    assert "REQUIRED_CAPABILITY_MISSING" not in {issue.code for issue in outcome.issues}
