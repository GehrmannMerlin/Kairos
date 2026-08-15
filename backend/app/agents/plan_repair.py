"""Deterministic, contract-aware context for one bounded plan repair call."""

from __future__ import annotations

from typing import Any

from app.plan.nodes import NodeDefinition, NodeRegistry
from app.plan.schemas import PlanGraphDraft, PlanNodeInstance, PlanValidationIssue


def _source_contract(
    node: PlanNodeInstance | None, definition: NodeDefinition | None
) -> dict[str, Any] | None:
    if node is None or definition is None:
        return None
    return {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "output_contract": [kind.value for kind in definition.output_contract],
    }


def _target_contract(
    node: PlanNodeInstance | None, definition: NodeDefinition | None
) -> dict[str, Any] | None:
    if node is None or definition is None:
        return None
    return {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "input_contract": [kind.value for kind in definition.input_contract],
    }


def build_plan_repair_context(
    original_graph: PlanGraphDraft,
    issues: list[PlanValidationIssue],
    node_registry: NodeRegistry,
) -> dict[str, Any]:
    """Return stable repair evidence without performing inference or validation."""

    nodes = {node.node_id: node for node in original_graph.nodes}
    issue_details: list[dict[str, Any]] = []
    for issue in issues:
        detail: dict[str, Any] = {
            "issue": issue.model_dump(mode="json", exclude_none=True),
        }

        source = nodes.get(issue.edge_from_node_id or "")
        target = nodes.get(issue.edge_to_node_id or "")
        source_definition = node_registry.get(source.node_type) if source else None
        target_definition = node_registry.get(target.node_type) if target else None
        source_contract = _source_contract(source, source_definition)
        target_contract = _target_contract(target, target_definition)
        if source_contract is not None:
            detail["source_node_contract"] = source_contract
        if target_contract is not None:
            detail["target_node_contract"] = target_contract

        if issue.parameter_path is not None:
            schema = issue.expected_schema or {}
            detail["parameter_contract"] = {
                "parameter_schema": schema,
                "required_fields": list(schema.get("required", [])),
                "actual_value_summary": issue.actual_value_summary or "",
            }
        issue_details.append(detail)

    return {
        "instruction": (
            "Return only one complete replacement graph JSON object. "
            "Do not return a patch, explanation, markdown, or partial nodes."
        ),
        "original_graph": original_graph.model_dump(mode="json"),
        "validator_issues": issue_details,
    }
