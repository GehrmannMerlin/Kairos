"""Capability-driven inference request policy behavior."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from math import ceil

import pytest
from app.domain.task_types import TaskType
from app.plan.schemas import PlanGraphDraft
from app.providers.inference_policy import (
    DEEPSEEK_CAPABILITY,
    InferenceIntent,
    InferenceRequestPolicy,
    ProviderCapability,
    resolve_inference_policy,
)
from app.providers.registry import get_model_definition, list_model_provider_definitions


def test_deepseek_structured_plan_policy_disables_thinking() -> None:
    """Removing the DeepSeek plan branch must expose the incident regression."""
    policy = resolve_inference_policy(
        intent=InferenceIntent.PLAN_STRUCTURED,
        capability=DEEPSEEK_CAPABILITY,
    )

    assert policy == InferenceRequestPolicy(
        response_format={"type": "json_object"},
        thinking={"type": "disabled"},
        max_tokens=4096,
    )


def test_deepseek_goal_extraction_does_not_inherit_plan_flags() -> None:
    """Applying plan tuning to Goal Understanding must fail this test."""
    policy = resolve_inference_policy(
        intent=InferenceIntent.GOAL_EXTRACTION,
        capability=DEEPSEEK_CAPABILITY,
    )

    assert policy.response_format == {"type": "json_object"}
    assert policy.thinking is None
    assert policy.max_tokens is None


def test_custom_agent_does_not_inherit_plan_flags() -> None:
    """Applying plan tuning to custom agents must fail this test."""
    policy = resolve_inference_policy(
        intent=InferenceIntent.CUSTOM_AGENT,
        capability=DEEPSEEK_CAPABILITY,
    )

    assert policy.response_format == {"type": "json_object"}
    assert policy.thinking is None
    assert policy.max_tokens is None


def test_non_deepseek_openai_compatible_plan_has_no_private_extension() -> None:
    """Sending DeepSeek-private fields to another compatible provider is a bug."""
    capability = get_model_definition("custom_openai_compatible").capability

    assert resolve_inference_policy(
        intent=InferenceIntent.PLAN_STRUCTURED,
        capability=capability,
    ) == InferenceRequestPolicy(response_format={"type": "json_object"})


def test_every_model_provider_declares_an_inference_capability() -> None:
    """A newly registered model provider must not bypass policy resolution."""
    definitions = list_model_provider_definitions()

    assert definitions
    assert all(isinstance(definition.capability, ProviderCapability) for definition in definitions)
    assert get_model_definition("deepseek").capability == DEEPSEEK_CAPABILITY


def test_request_policy_is_frozen() -> None:
    """Mutating a resolved policy after resolution would make requests nondeterministic."""
    policy = resolve_inference_policy(
        intent=InferenceIntent.PLAN_STRUCTURED,
        capability=DEEPSEEK_CAPABILITY,
    )

    with pytest.raises(FrozenInstanceError):
        policy.max_tokens = 1  # type: ignore[misc]


def test_complete_ten_node_plan_fits_half_of_output_budget() -> None:
    """A representative full graph must leave at least two-times output headroom."""
    node_types = [
        "source_search",
        "access_rules_check",
        "link_discovery",
        "fetch",
        "browser_render",
        "extract",
        "normalize",
        "deduplicate",
        "validate",
        "generate_artifact",
    ]
    resource_kinds = [
        "candidate",
        "url",
        "url",
        "snapshot",
        "snapshot",
        "record",
        "record",
        "record",
        "record",
    ]
    payload = {
        "schema_version": "m08.1",
        "task_id": 42,
        "spec_version": 3,
        "task_type": TaskType.HYBRID,
        "nodes": [
            {
                "node_id": f"n{index}",
                "node_type": node_type,
                "definition_version": "1.0.0",
                "parameters": {},
                "depends_on": [] if index == 1 else [f"n{index - 1}"],
                "optional": False,
                "fail_policy": "block",
            }
            for index, node_type in enumerate(node_types, start=1)
        ],
        "edges": [
            {
                "from_node_id": f"n{index}",
                "to_node_id": f"n{index + 1}",
                "resource_refs": [{"kind": kind, "ref_key": f"resource:{index}"}],
            }
            for index, kind in enumerate(resource_kinds, start=1)
        ],
        "reasoning_summary": "按来源发现、抓取、抽取、清洗、校验和交付顺序执行。",
    }
    graph = PlanGraphDraft.model_validate(payload)
    serialized = json.dumps(
        graph.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
    conservative_tokens = ceil(len(serialized.encode("utf-8")) / 3)

    assert conservative_tokens <= 2048
    assert conservative_tokens * 2 <= 4096
