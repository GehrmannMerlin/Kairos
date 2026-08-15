"""Immutable Production executor capability contract."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.plan.executors import NODE_EXECUTORS
from app.plan.nodes import NodeType, ResourceClass


@dataclass(frozen=True)
class ExecutorCapability:
    node_type: NodeType
    resource_class: ResourceClass | None
    task_queue_role: str
    implementation_id: str


CAPABILITY_MANIFEST_VERSION = "m08-production-v1"
PRODUCTION_EXECUTOR_CAPABILITIES: Sequence[ExecutorCapability] = (
    ExecutorCapability(
        NodeType.SOURCE_SEARCH, ResourceClass.LLM_SEARCH, "llm_search", "search-service-v1"
    ),
    ExecutorCapability(NodeType.ACCESS_RULES_CHECK, ResourceClass.CORE, "core", "access-rules-v1"),
    ExecutorCapability(NodeType.LINK_DISCOVERY, ResourceClass.CORE, "core", "link-discovery-v1"),
    ExecutorCapability(NodeType.FETCH, ResourceClass.HTTP, "http", "http-fetch-v1"),
    ExecutorCapability(
        NodeType.BROWSER_RENDER, ResourceClass.BROWSER, "browser", "browser-render-v1"
    ),
    ExecutorCapability(NodeType.EXTRACT, ResourceClass.CORE, "core", "extraction-v1"),
    ExecutorCapability(NodeType.NORMALIZE, ResourceClass.CORE, "core", "normalize-v1"),
    ExecutorCapability(NodeType.DEDUPLICATE, ResourceClass.CORE, "core", "deduplicate-v1"),
    ExecutorCapability(NodeType.VALIDATE, ResourceClass.CORE, "core", "validation-v1"),
    ExecutorCapability(
        NodeType.GENERATE_ARTIFACT, ResourceClass.CORE, "core", "artifact-export-v1"
    ),
)


def supported_node_types() -> set[NodeType]:
    """Return Production-declared node types, independent of fixture registrations."""
    return {capability.node_type for capability in PRODUCTION_EXECUTOR_CAPABILITIES}


def assert_runtime_executor_manifest() -> None:
    """Fail startup if real executor registrations diverge from the Production contract."""
    declared = supported_node_types()
    registered = set(NODE_EXECUTORS)
    missing = sorted(node_type.value for node_type in declared - registered)
    extra = sorted(node_type.value for node_type in registered - declared)
    if missing or extra:
        raise RuntimeError(f"Runtime executor manifest mismatch: missing={missing}, extra={extra}")
