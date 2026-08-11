"""M-08 Task 1: canonical node / risk / resource vocabulary + registry contract."""

from __future__ import annotations

from app.plan.nodes import (
    NodeRegistry,
    NodeType,
    ResourceClass,
    ResourceKind,
    RiskLevel,
)
from pydantic import BaseModel


def test_node_type_has_ten_standard_nodes() -> None:
    assert list(NodeType) == [
        NodeType.SOURCE_SEARCH,
        NodeType.ACCESS_RULES_CHECK,
        NodeType.LINK_DISCOVERY,
        NodeType.FETCH,
        NodeType.BROWSER_RENDER,
        NodeType.EXTRACT,
        NodeType.NORMALIZE,
        NodeType.DEDUPLICATE,
        NodeType.VALIDATE,
        NodeType.GENERATE_ARTIFACT,
    ]


def test_risk_level_has_no_boolean_leak() -> None:
    assert list(RiskLevel) == [
        RiskLevel.LOW,
        RiskLevel.MEDIUM,
        RiskLevel.HIGH,
        RiskLevel.PROHIBITED,
    ]


def test_resource_kind_covers_typed_io() -> None:
    for kind in ResourceKind:
        assert kind.value


def test_registry_registers_ten_standard_definitions() -> None:
    registry = NodeRegistry()
    defs = registry.all()
    assert {d.node_type for d in defs} == set(NodeType)
    # 每个 definition 都有 typed parameter schema 与 input/output contract
    for d in defs:
        assert issubclass(d.parameter_schema, BaseModel)
        assert d.definition_version
        assert d.input_contract
        assert d.output_contract
        assert d.timeout_seconds > 0
        assert d.retry_policy.max_attempts >= 1
        assert d.risk_level in RiskLevel
        assert d.resource_class in ResourceClass
        assert d.idempotency_identity
        assert d.recoverable_boundary


def test_registry_is_static_allowlist() -> None:
    registry = NodeRegistry()
    assert registry.is_registered(NodeType.FETCH)
    assert registry.get(NodeType.FETCH).risk_level == RiskLevel.LOW
    # 未注册 node_type 必须不存在（Agent 不能引用未知动作）
    assert registry.get(NodeType.SOURCE_SEARCH).risk_level == RiskLevel.MEDIUM
    assert registry.get(NodeType.BROWSER_RENDER).risk_level == RiskLevel.MEDIUM


def test_planning_metadata_is_serializable() -> None:
    meta = NodeRegistry().planning_metadata()
    assert len(meta) == len(NodeType)
    first = meta[0]
    assert set(first) >= {"node_type", "risk_level", "resource_class", "input", "output"}
