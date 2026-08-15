"""Contract-aware plan repair context tests."""

from __future__ import annotations

from app.agents.plan_repair import build_plan_repair_context
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft
from app.plan.validator import validate_plan


def _invalid_graph() -> PlanGraphDraft:
    return PlanGraphDraft.model_validate(
        {
            "schema_version": "m08.1",
            "task_id": 1,
            "spec_version": 1,
            "task_type": "SPECIFIED_SOURCE",
            "nodes": [
                {
                    "node_id": "fetch-1",
                    "node_type": "fetch",
                    "definition_version": "1.0.0",
                    "parameters": {
                        "url_template": ["not", "a", "string"],
                        "unexpected": "must be removed",
                    },
                    "depends_on": [],
                },
                {
                    "node_id": "normalize-1",
                    "node_type": "normalize",
                    "definition_version": "1.0.0",
                    "parameters": {},
                    "depends_on": ["fetch-1"],
                },
            ],
            "edges": [
                {
                    "from_node_id": "fetch-1",
                    "to_node_id": "normalize-1",
                    "resource_refs": [{"kind": "snapshot", "ref_key": "snap:1"}],
                }
            ],
        }
    )


def test_repair_context_contains_original_graph_issues_and_contracts() -> None:
    registry = NodeRegistry()
    graph = _invalid_graph()
    outcome = validate_plan(graph, {}, registry)

    context = build_plan_repair_context(graph, outcome.issues, registry)

    assert context["original_graph"] == graph.model_dump(mode="json")
    assert "complete replacement graph" in context["instruction"]

    details = {item["issue"]["code"]: item for item in context["validator_issues"]}
    assert set(details) >= {"PARAMETER_SCHEMA_INVALID", "RESOURCE_EDGE_INCOMPATIBLE"}

    parameter = details["PARAMETER_SCHEMA_INVALID"]
    assert parameter["issue"]["node_id"] == "fetch-1"
    assert parameter["issue"]["parameter_path"] == "nodes.fetch-1.parameters"
    assert "url_template" in parameter["parameter_contract"]["required_fields"]
    assert "unexpected" in parameter["parameter_contract"]["actual_value_summary"]

    edge = details["RESOURCE_EDGE_INCOMPATIBLE"]
    assert edge["issue"]["edge_from_node_id"] == "fetch-1"
    assert edge["issue"]["edge_to_node_id"] == "normalize-1"
    assert edge["source_node_contract"]["output_contract"] == ["snapshot"]
    assert edge["target_node_contract"]["input_contract"] == ["record", "spec"]


def test_repair_context_is_deterministic() -> None:
    registry = NodeRegistry()
    graph = _invalid_graph()
    issues = validate_plan(graph, {}, registry).issues

    assert build_plan_repair_context(graph, issues, registry) == build_plan_repair_context(
        graph, issues, registry
    )
