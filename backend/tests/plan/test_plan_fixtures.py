"""M-08 Task 3: ≥10 组合法/非法 Plan fixture 契约表（parameterized，单文件）。"""

from __future__ import annotations

import pytest
from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry
from app.plan.schemas import (
    PlanEdge,
    PlanGraphDraft,
    PlanNodeInstance,
    PlanValidationResult,
    ResourceRef,
)
from app.plan.validator import validate_plan


def _node(node_id: str, node_type: str, **kw) -> PlanNodeInstance:
    return PlanNodeInstance(
        node_id=node_id,
        node_type=node_type,
        definition_version=kw.pop("definition_version", "1.0.0"),
        parameters=kw.pop("parameters", {}),
        depends_on=kw.pop("depends_on", []),
        optional=kw.pop("optional", False),
        fail_policy=kw.pop("fail_policy", "block"),
    )


def _edge(a: str, b: str, kind: str = "snapshot", ref: str = "snap:1") -> PlanEdge:
    return PlanEdge(
        from_node_id=a, to_node_id=b, resource_refs=[ResourceRef(kind=kind, ref_key=ref)]
    )


_SPEC = SpecDraftPayload(
    task_type=TaskType.SPECIFIED_SOURCE,
    goal="抓取指定网站公司信息",
    fields=[{"name": "公司名", "type": "text", "required": True}],
    source_scope={
        "mode": "SPECIFIED_SOURCE",
        "seed_urls": ["https://example.com"],
        "source_hints": [],
    },
    completion_conditions=[{"kind": "range_covered", "target": 10}],
    advanced_settings={"max_pages": 100},
).model_dump(mode="json")


def _draft(
    nodes: list[PlanNodeInstance],
    edges: list[PlanEdge] | None = None,
    task_type=TaskType.SPECIFIED_SOURCE,
) -> PlanGraphDraft:
    return PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=task_type,
        nodes=nodes,
        edges=edges or [],
    )


CASES = [
    # 01 合法 specified-source Plan
    (
        "valid_specified_source",
        _draft(
            [
                _node("n1", "fetch", parameters={"url_template": "https://example.com/item/{id}"}),
                _node("n2", "extract", parameters={"fields": ["公司名"]}, depends_on=["n1"]),
            ]
        ),
        PlanValidationResult.VALID,
    ),
    # 02 合法 exploratory Plan（含 SourceSearch，search available）
    (
        "valid_exploratory",
        _draft(
            [
                _node(
                    "n1",
                    "source_search",
                    parameters={"query": "自动化设备", "max_results": 20},
                ),
                _node(
                    "n2", "fetch", depends_on=["n1"], parameters={"url_template": "https://{site}/"}
                ),
                _node("n3", "extract", parameters={"fields": ["公司名"]}, depends_on=["n2"]),
            ],
            task_type=TaskType.EXPLORATORY,
        ),
        PlanValidationResult.VALID,
    ),
    # 03 合法 hybrid Plan
    (
        "valid_hybrid",
        _draft(
            [
                _node("n1", "source_search", parameters={"query": "机器人"}),  # noqa: SIM108
                _node(
                    "n2", "fetch", depends_on=["n1"], parameters={"url_template": "https://{site}/"}
                ),
                _node("n3", "extract", parameters={"fields": ["公司名"]}, depends_on=["n2"]),
                _node("n4", "deduplicate", depends_on=["n3"]),
            ],
            task_type=TaskType.HYBRID,
        ),
        PlanValidationResult.VALID,
    ),
    # 04 未注册 node
    (
        "unregistered_node",
        _draft([_node("n1", "ssh_into_server")]),
        PlanValidationResult.INVALID,
    ),
    # 05 重复 node id
    (
        "duplicate_node_id",
        _draft(
            [
                _node("n1", "fetch", parameters={"url_template": "https://example.com/{id}"}),
                _node("n1", "extract", parameters={"fields": ["公司名"]}),
            ]
        ),
        PlanValidationResult.INVALID,
    ),
    # 06 依赖缺失
    (
        "missing_dependency",
        _draft(
            [
                _node(
                    "n1",
                    "fetch",
                    depends_on=["ghost"],
                    parameters={"url_template": "https://example.com/{id}"},
                )
            ]
        ),
        PlanValidationResult.INVALID,
    ),
    # 07 环
    (
        "cycle",
        _draft(
            [
                _node(
                    "n1",
                    "fetch",
                    depends_on=["n2"],
                    parameters={"url_template": "https://example.com/{id}"},
                ),
                _node("n2", "extract", parameters={"fields": ["公司名"]}, depends_on=["n1"]),
            ]
        ),
        PlanValidationResult.INVALID,
    ),
    # 08 参数 schema 非法（未知参数键在严格契约下被拒）
    (
        "invalid_parameter_schema",
        _draft([_node("n1", "source_search", parameters={"query": "x", "not_a_field": 1})]),
        PlanValidationResult.INVALID,
    ),
    # 09 资源边不兼容（fetch 输出 snapshot 喂给 validate 需要 record+evidence）
    (
        "incompatible_resource_edge",
        _draft(
            [
                _node("n1", "fetch", parameters={"url_template": "https://example.com/{id}"}),
                _node("n2", "validate", parameters={"min_required_fields": 1}, depends_on=["n1"]),
            ],
            [_edge("n1", "n2", kind="snapshot")],
        ),
        PlanValidationResult.INVALID,
    ),
    # 10 Spec Version 不匹配
    (
        "spec_version_mismatch",
        PlanGraphDraft(
            task_id=1,
            spec_version=999,
            task_type=TaskType.SPECIFIED_SOURCE,
            nodes=[_node("n1", "fetch", parameters={"url_template": "https://example.com/{id}"})],
        ),
        PlanValidationResult.INVALID,
    ),
    # 11 扩大 Spec scope → REQUIRES_NEW_SPEC
    (
        "scope_expansion",
        _draft(
            [_node("n1", "fetch", parameters={"url_template": "https://other-domain.com/{id}"})]
        ),
        PlanValidationResult.REQUIRES_NEW_SPEC,
    ),
    # 12 改变核心字段含义 → REQUIRES_NEW_SPEC
    (
        "field_semantics_change",
        _draft([_node("n1", "extract", parameters={"fields": ["不应存在的字段"]})]),
        PlanValidationResult.REQUIRES_NEW_SPEC,
    ),
    # 13 降低质量要求 → REQUIRES_NEW_SPEC
    (
        "quality_reduction",
        _draft([_node("n1", "validate", parameters={"min_required_fields": 0})]),
        PlanValidationResult.REQUIRES_NEW_SPEC,
    ),
    # 14 同域内非公开/凭据访问高风险 → REQUIRES_APPROVAL（不越界）
    (
        "credential_high_risk",
        _draft(
            [
                _node(
                    "n1",
                    "fetch",
                    parameters={
                        "url_template": "https://example.com/private/{id}",
                        "non_public": True,
                        "credential_ref": "cred:site-login",
                    },
                )
            ]
        ),
        PlanValidationResult.REQUIRES_APPROVAL,
    ),
    # 15 禁止越界动作 → PROHIBITED
    (
        "prohibited_bypass",
        _draft(
            [
                _node(
                    "n1",
                    "fetch",
                    parameters={"url_template": "https://example.com/{id}", "bypass_captcha": True},
                )
            ]
        ),
        PlanValidationResult.PROHIBITED,
    ),
]


@pytest.mark.parametrize("name,draft,expected", CASES, ids=[c[0] for c in CASES])
def test_plan_fixture_contracts(name, draft, expected) -> None:
    outcome = validate_plan(draft, _SPEC, NodeRegistry(), spec_version=1)
    assert outcome.result == expected, f"{name}: {[i.model_dump() for i in outcome.issues]}"


def test_extract_fields_object_form_does_not_crash() -> None:
    """Regression (Gate-2 real provider): 真实 LLM 可能把 EXTRACT parameters.fields
    输出为对象数组（[{"name": "..."}]）。此前 spec_fields 检查对 dict 元素做
    `f not in set` 触发 unhashable TypeError → 500。正确行为：不崩溃，且由参数
    schema 校验判为 INVALID（PARAMETER_SCHEMA_INVALID），repair 循环再修正。"""
    draft = _draft(
        [
            _node(
                "n1",
                "extract",
                parameters={"fields": [{"name": "公司名", "type": "text"}]},
                depends_on=["seed"],
            ),
            _node("seed", "fetch", parameters={"url_template": "https://example.com/x"}),
        ],
        edges=[_edge("seed", "n1", kind="snapshot", ref="snap:1")],
    )
    outcome = validate_plan(draft, _SPEC, NodeRegistry(), spec_version=1)
    assert outcome.result == PlanValidationResult.INVALID
    assert any(i.code == "PARAMETER_SCHEMA_INVALID" for i in outcome.issues)


def test_extract_fields_string_list_passes_field_semantics() -> None:
    """EXTRACT parameters.fields 为字符串数组且落在已确认字段内 → 不产生字段语义 issue。"""
    draft = _draft(
        [
            _node(
                "n1",
                "extract",
                parameters={"fields": ["公司名"]},
                depends_on=["seed"],
            ),
            _node("seed", "fetch", parameters={"url_template": "https://example.com/x"}),
        ],
        edges=[_edge("seed", "n1", kind="snapshot", ref="snap:1")],
    )
    outcome = validate_plan(draft, _SPEC, NodeRegistry(), spec_version=1)
    assert not any(i.code == "SPEC_FIELD_SEMANTICS" for i in outcome.issues)
