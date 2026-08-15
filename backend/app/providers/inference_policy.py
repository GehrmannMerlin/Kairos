"""Pure capability-driven request policy for model inference."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class InferenceIntent(StrEnum):
    PLAN_STRUCTURED = "PLAN_STRUCTURED"
    GOAL_EXTRACTION = "GOAL_EXTRACTION"
    CUSTOM_AGENT = "CUSTOM_AGENT"


@dataclass(frozen=True)
class ProviderCapability:
    supports_json_object: bool
    supports_thinking_control: bool
    plan_thinking_mode: Literal["disabled"] | None = None


@dataclass(frozen=True)
class InferenceRequestPolicy:
    response_format: dict[str, str] | None = None
    thinking: dict[str, str] | None = None
    max_tokens: int | None = None


DEFAULT_PROVIDER_CAPABILITY = ProviderCapability(
    supports_json_object=False,
    supports_thinking_control=False,
)
JSON_OBJECT_CAPABILITY = ProviderCapability(
    supports_json_object=True,
    supports_thinking_control=False,
)
DEEPSEEK_CAPABILITY = ProviderCapability(
    supports_json_object=True,
    supports_thinking_control=True,
    plan_thinking_mode="disabled",
)


def resolve_inference_policy(
    *, intent: InferenceIntent, capability: ProviderCapability
) -> InferenceRequestPolicy:
    """Resolve request options without settings, credentials, clocks, or I/O."""
    response_format = {"type": "json_object"} if capability.supports_json_object else None
    if (
        intent is InferenceIntent.PLAN_STRUCTURED
        and capability.supports_thinking_control
        and capability.plan_thinking_mode == "disabled"
    ):
        return InferenceRequestPolicy(
            response_format=response_format,
            thinking={"type": "disabled"},
            max_tokens=4096,
        )
    return InferenceRequestPolicy(response_format=response_format)
