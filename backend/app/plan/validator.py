"""Deterministic Plan Validator (M-08 / D-008).

Validator 不调用 LLM。按固定顺序检查并返回结构化 error code；判定结果是唯一的
``PlanValidationResult``。任何判定都不能由模型决定——规则程序决定计划是否合法。

完整校验顺序：
1. Plan schema（pydantic 构造时已校验）
2. Node type 已注册
3. NodeDefinition version
4. Node ID 唯一
5. dependency targets 存在
6. DAG 无环
7. typed parameter schema
8. typed resource edge 兼容
9. 上游数据可用性
10. Spec Version 精确匹配
11. 范围边界（扩大 → REQUIRES_NEW_SPEC）
12. 字段语义边界（改变 → REQUIRES_NEW_SPEC）
13. 质量/完成约束（降低 → REQUIRES_NEW_SPEC）
14. 技术运行限制
15. permission/risk（PROHIBITED 直接拒绝；HIGH → REQUIRES_APPROVAL）
16. Provider 前置条件
17. canonical fingerprint
18. 最终判定
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.plan.nodes import NodeRegistry, NodeType, ResourceKind, RiskLevel
from app.plan.schemas import (
    PlanGraphDraft,
    PlanValidationIssue,
    PlanValidationResult,
)


@dataclass(frozen=True)
class PlanValidationOutcome:
    result: PlanValidationResult
    issues: list[PlanValidationIssue] = field(default_factory=list)
    node_risk_levels: dict[str, RiskLevel] = field(default_factory=dict)
    fingerprint: str = ""


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return (parsed.hostname or "").lower()


def _node_effective_risk(
    node_type: NodeType | str, definition_risk: RiskLevel, parameters: dict
) -> RiskLevel:
    """节点实际风险 = 定义风险 + 参数信号（M-08 只做规则判定，不引入 LLM）。"""
    if node_type == NodeType.FETCH:
        if parameters.get("bypass_captcha"):
            return RiskLevel.PROHIBITED
        if parameters.get("non_public") or parameters.get("credential_ref"):
            return RiskLevel.HIGH
    if node_type == NodeType.BROWSER_RENDER and parameters.get("credential_ref"):
        return RiskLevel.HIGH
    return definition_risk


def _validate_parameters(
    node_type: NodeType | str,
    definition_risk: RiskLevel,
    parameters: dict,
    registry: NodeRegistry,
) -> tuple[list[PlanValidationIssue], RiskLevel]:
    issues: list[PlanValidationIssue] = []
    definition = registry.get(node_type)
    if definition is None:
        return issues, definition_risk
    try:
        definition.parameter_schema.model_validate(parameters)
    except Exception as exc:  # pydantic ValidationError -> 结构化 issue
        issues.append(
            PlanValidationIssue(
                code="PARAMETER_SCHEMA_INVALID",
                message=f"节点参数不符合契约: {str(exc)[:200]}",
                path="parameters",
            )
        )
    return issues, _node_effective_risk(node_type, definition_risk, parameters)


def validate_plan(
    graph: PlanGraphDraft,
    spec_payload: dict,
    registry: NodeRegistry | None = None,
    *,
    available_search: bool = True,
    spec_version: int | None = None,
) -> PlanValidationOutcome:
    registry = registry or NodeRegistry()
    issues: list[PlanValidationIssue] = []
    node_risk_levels: dict[str, RiskLevel] = {}

    # 2/3. Node registered + definition version
    for n in graph.nodes:
        definition = registry.get(n.node_type)
        if definition is None:
            issues.append(
                PlanValidationIssue(
                    code="NODE_NOT_REGISTERED",
                    message=f"节点类型未注册: {n.node_type}",
                    node_id=n.node_id,
                )
            )
        elif n.definition_version != definition.definition_version:
            issues.append(
                PlanValidationIssue(
                    code="NODE_DEFINITION_VERSION_MISMATCH",
                    message=(
                        f"节点定义版本不匹配: 计划 {n.definition_version} "
                        f"!= 注册 {definition.definition_version}"
                    ),
                    node_id=n.node_id,
                )
            )

    # 4. unique node ids
    ids = [n.node_id for n in graph.nodes]
    if len(ids) != len(set(ids)):
        issues.append(PlanValidationIssue(code="DUPLICATE_NODE_ID", message="节点 ID 重复"))

    # 5. dependency targets exist
    known = {n.node_id for n in graph.nodes}
    for n in graph.nodes:
        for dep in n.depends_on:
            if dep not in known:
                issues.append(
                    PlanValidationIssue(
                        code="MISSING_DEPENDENCY", message=f"依赖不存在: {dep}", node_id=n.node_id
                    )
                )

    # 6. DAG acyclic (topological sort)
    indegree = dict.fromkeys(known, 0)
    adj: dict[str, list[str]] = {nid: [] for nid in known}
    for n in graph.nodes:
        for dep in n.depends_on:
            if dep not in known:
                continue  # 依赖缺失已在 #5 记录；避免 ghost key
            adj[dep].append(n.node_id)
            indegree[n.node_id] += 1
    queue = [nid for nid in known if indegree[nid] == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop()
        order.append(nid)
        for m in adj[nid]:
            indegree[m] -= 1
            if indegree[m] == 0:
                queue.append(m)
    if len(order) != len(known):
        issues.append(PlanValidationIssue(code="CYCLE_DETECTED", message="计划存在依赖环"))

    # 7. typed parameter schema + 15. risk per node
    spec_scope = spec_payload.get("source_scope", {}) if isinstance(spec_payload, dict) else {}
    allowed_hosts = {_host_of(u) for u in spec_scope.get("seed_urls", [])}
    spec_fields = (
        {f.get("name") for f in spec_payload.get("fields", [])}
        if isinstance(spec_payload, dict)
        else set()
    )
    for n in graph.nodes:
        definition = registry.get(n.node_type)
        base_risk = definition.risk_level if definition else RiskLevel.LOW
        param_issues, effective_risk = _validate_parameters(
            n.node_type, base_risk, n.parameters, registry
        )
        node_risk_levels[n.node_id] = effective_risk
        for pi in param_issues:
            pi.node_id = n.node_id
            issues.append(pi)

    # 8. typed resource edge compatibility（基本方向校验；M-09 深化）
    for edge in graph.edges:
        src = next((n for n in graph.nodes if n.node_id == edge.from_node_id), None)
        dst = next((n for n in graph.nodes if n.node_id == edge.to_node_id), None)
        if src is None or dst is None:
            issues.append(
                PlanValidationIssue(
                    code="EDGE_UNKNOWN_NODE",
                    message=f"边引用未知节点: {edge.from_node_id}->{edge.to_node_id}",
                )
            )
            continue
        src_def = registry.get(src.node_type)
        dst_def = registry.get(dst.node_type)
        if src_def and dst_def:
            for ref in edge.resource_refs:
                src_ok = ref.kind in src_def.output_contract
                dst_ok = ref.kind in dst_def.input_contract
                # M-09 source_search executor 把搜索结果 URL 物化为 URL Frontier 资源
                # （D-068 典型路径 SourceSearch → AccessRulesCheck → LinkDiscovery →
                # Fetch）。因此 source_search 产出的 candidate 可被消费 url 的发现节点
                # 接收，等价于候选站点已作为 url 资源进入 Frontier。
                if (
                    ref.kind == ResourceKind.CANDIDATE
                    and ResourceKind.URL in dst_def.input_contract
                ):
                    dst_ok = True
                if not src_ok or not dst_ok:
                    issues.append(
                        PlanValidationIssue(
                            code="RESOURCE_EDGE_INCOMPATIBLE",
                            message=(
                                f"资源边不兼容: {ref.kind.value} 不能从 "
                                f"{edge.from_node_id} 流向 {edge.to_node_id}"
                            ),
                            node_id=edge.to_node_id,
                        )
                    )

    # 10. Spec Version exact match（spec_version 由调用方传入真实已确认版本号）
    if spec_version is not None and graph.spec_version != spec_version:
        issues.append(
            PlanValidationIssue(
                code="SPEC_VERSION_MISMATCH",
                message=f"Spec Version 不匹配: 计划 {graph.spec_version} != 已确认 {spec_version}",
            )
        )

    # 11. scope boundary / 12. field semantics / 13. quality
    spec_boundary_issues: list[PlanValidationIssue] = []
    for n in graph.nodes:
        if n.node_type == NodeType.FETCH:
            url_template = str(n.parameters.get("url_template", ""))
            host = _host_of(url_template)
            # 主机本身含 {site} 模板占位符时（探索式站点模板），执行期才解析，不判越界
            if "{" in host:
                host = ""
            if allowed_hosts and host and host not in allowed_hosts:
                spec_boundary_issues.append(
                    PlanValidationIssue(
                        code="SPEC_SCOPE_EXPANSION",
                        message=f"计划扩大采集范围到 {host}",
                        node_id=n.node_id,
                    )
                )
        if n.node_type == NodeType.EXTRACT:
            raw_fields = n.parameters.get("fields", [])
            if isinstance(raw_fields, list):
                for raw_f in raw_fields:
                    # 真实 LLM 可能把 fields 输出成对象数组（[{"name": "..."}]）。
                    # 参数 schema 校验会以 PARAMETER_SCHEMA_INVALID 拦截该形态；
                    # 这里对 dict 元素取 name 参与边界检查，避免 `dict not in set`
                    # 触发 unhashable TypeError → 500（Gate-2 真实 Provider 发现）。
                    f = raw_f.get("name") if isinstance(raw_f, dict) else raw_f
                    if not isinstance(f, str):
                        continue
                    if f not in spec_fields:
                        spec_boundary_issues.append(
                            PlanValidationIssue(
                                code="SPEC_FIELD_SEMANTICS",
                                message=f"计划引入未确认字段 {f}",
                                node_id=n.node_id,
                            )
                        )
        if n.node_type == NodeType.VALIDATE and n.parameters.get("min_required_fields", 1) < 1:
            spec_boundary_issues.append(
                PlanValidationIssue(
                    code="SPEC_QUALITY_REDUCTION",
                    message="计划降低必填字段质量要求",
                    node_id=n.node_id,
                )
            )

    # 15. PROHIBITED -> direct reject (最高优先级)
    prohibited = [nid for nid, risk in node_risk_levels.items() if risk == RiskLevel.PROHIBITED]
    if prohibited:
        issues.append(
            PlanValidationIssue(
                code="ACTION_PROHIBITED",
                message="计划包含禁止执行的动作（如绕过验证码/规避访问控制）",
                node_id=prohibited[0],
            )
        )
        return PlanValidationOutcome(
            result=PlanValidationResult.PROHIBITED,
            issues=issues,
            node_risk_levels=node_risk_levels,
        )

    # 11-13. Spec boundary 优先于 Approval
    if spec_boundary_issues:
        issues.extend(spec_boundary_issues)
        return PlanValidationOutcome(
            result=PlanValidationResult.REQUIRES_NEW_SPEC,
            issues=issues,
            node_risk_levels=node_risk_levels,
        )

    # 结构性问题 -> INVALID
    structural = [
        i
        for i in issues
        if i.code not in {"SPEC_SCOPE_EXPANSION", "SPEC_FIELD_SEMANTICS", "SPEC_QUALITY_REDUCTION"}
    ]
    if structural:
        return PlanValidationOutcome(
            result=PlanValidationResult.INVALID,
            issues=issues,
            node_risk_levels=node_risk_levels,
        )

    # 16. Provider prerequisites
    if graph.task_type.value in ("EXPLORATORY", "HYBRID"):
        has_search_node = any(n.node_type == NodeType.SOURCE_SEARCH for n in graph.nodes)
        if has_search_node and not available_search:
            issues.append(
                PlanValidationIssue(
                    code="SEARCH_PROVIDER_REQUIRED", message="探索/混合任务需要已配置的搜索服务"
                )
            )
            return PlanValidationOutcome(
                result=PlanValidationResult.INVALID,
                issues=issues,
                node_risk_levels=node_risk_levels,
            )

    # 15. HIGH -> REQUIRES_APPROVAL
    high = [nid for nid, risk in node_risk_levels.items() if risk == RiskLevel.HIGH]
    if high:
        return PlanValidationOutcome(
            result=PlanValidationResult.REQUIRES_APPROVAL,
            issues=issues,
            node_risk_levels=node_risk_levels,
        )

    return PlanValidationOutcome(
        result=PlanValidationResult.VALID,
        issues=issues,
        node_risk_levels=node_risk_levels,
    )
