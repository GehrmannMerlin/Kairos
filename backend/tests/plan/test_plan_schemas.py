"""M-08 Task 1: typed PlanGraphDraft / validation result schemas."""

from __future__ import annotations

import pytest
from app.domain.task_types import TaskType
from app.plan.nodes import NodeType, ResourceKind
from app.plan.schemas import (
    PlanEdge,
    PlanGraphDraft,
    PlanNodeInstance,
    PlanValidationResult,
    ResourceRef,
)
from pydantic import ValidationError


def _node(
    node_id: str, node_type: NodeType, depends_on: list[str] | None = None
) -> PlanNodeInstance:
    return PlanNodeInstance(
        node_id=node_id,
        node_type=node_type,
        definition_version="1.0.0",
        parameters={},
        depends_on=depends_on or [],
    )


def test_plan_graph_draft_roundtrip() -> None:
    draft = PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.SPECIFIED_SOURCE,
        nodes=[_node("n1", NodeType.FETCH), _node("n2", NodeType.EXTRACT, ["n1"])],
        edges=[
            PlanEdge(
                from_node_id="n1",
                to_node_id="n2",
                resource_refs=[ResourceRef(kind=ResourceKind.SNAPSHOT, ref_key="snap:1")],
            )
        ],
    )
    data = draft.model_dump(mode="json")
    assert data["nodes"][1]["depends_on"] == ["n1"]
    assert data["edges"][0]["resource_refs"][0]["kind"] == "snapshot"


def test_plan_graph_draft_rejects_unknown_node_type() -> None:
    with pytest.raises(ValidationError):
        PlanGraphDraft(
            task_id=1,
            spec_version=1,
            task_type=TaskType.EXPLORATORY,
            nodes=[_node("n1", "not_a_node")],  # type: ignore[arg-type]
            edges=[],
        )


def test_validation_result_is_single_canonical_enum() -> None:
    assert list(PlanValidationResult) == [
        PlanValidationResult.VALID,
        PlanValidationResult.REQUIRES_APPROVAL,
        PlanValidationResult.REQUIRES_NEW_SPEC,
        PlanValidationResult.INVALID,
        PlanValidationResult.PROHIBITED,
    ]
