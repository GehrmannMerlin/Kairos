"""Source-mode invariants for generated and frozen plans."""

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
            "source_hints": ["历史提示"],
        },
    ).model_dump(mode="json")


def _hybrid_graph_without_search() -> PlanGraphDraft:
    return PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.HYBRID,
        nodes=[
            _node("fetch", "fetch", parameters={"url_template": "https://example.com/{id}"}),
            _node("extract", "extract", parameters={"fields": ["公司名"]}),
        ],
    )


def _specified_graph() -> PlanGraphDraft:
    return PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.SPECIFIED_SOURCE,
        nodes=[
            _node("fetch", "fetch", parameters={"url_template": "https://example.com/{id}"}),
        ],
    )


def _source_search_graph(*, task_type: TaskType, late: bool) -> PlanGraphDraft:
    source_dependencies = ["fetch"] if late else []
    return PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=task_type,
        nodes=[
            _node(
                "source",
                "source_search",
                parameters={"query": "公司信息"},
                depends_on=source_dependencies,
            ),
            _node(
                "fetch",
                "fetch",
                parameters={"url_template": "https://example.com/{id}"},
            ),
        ],
    )


def test_hybrid_plan_requires_source_search() -> None:
    outcome = validate_plan(
        _hybrid_graph_without_search(),
        _spec(task_type=TaskType.HYBRID, seed_urls=[]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.INVALID
    assert "SOURCE_SEARCH_REQUIRED" in {issue.code for issue in outcome.issues}


def test_specified_plan_rejects_empty_seed_even_for_historical_spec() -> None:
    outcome = validate_plan(
        _specified_graph(),
        _spec(task_type=TaskType.SPECIFIED_SOURCE, seed_urls=[]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.INVALID
    assert "EXECUTION_INPUT_UNMATERIALIZABLE" in {issue.code for issue in outcome.issues}


@pytest.mark.parametrize(
    "seed_urls",
    [
        ["ftp://example.com/data"],
        ["   "],
        ["https://example.com/{page}"],
        ["https://user:password@example.com/data"],
    ],
)
def test_specified_plan_rejects_non_materializable_historical_seed_urls(
    seed_urls: list[str],
) -> None:
    outcome = validate_plan(
        _specified_graph(),
        _spec(task_type=TaskType.SPECIFIED_SOURCE, seed_urls=seed_urls),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.INVALID
    assert "EXECUTION_INPUT_UNMATERIALIZABLE" in {issue.code for issue in outcome.issues}


@pytest.mark.parametrize("task_type", [TaskType.EXPLORATORY, TaskType.HYBRID])
@pytest.mark.parametrize("late", [False, True], ids=["disconnected", "late"])
def test_search_must_precede_every_resource_consuming_path(task_type: TaskType, late: bool) -> None:
    outcome = validate_plan(
        _source_search_graph(task_type=task_type, late=late),
        _spec(task_type=task_type, seed_urls=[]),
        NodeRegistry(),
    )
    assert outcome.result is PlanValidationResult.INVALID
    assert "SOURCE_SEARCH_REQUIRED" in {issue.code for issue in outcome.issues}
