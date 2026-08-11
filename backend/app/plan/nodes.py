"""Canonical node vocabulary + typed NodeDefinition registry (M-08 / D-008).

Node Registry 是代码注册的静态允许列表。Agent 只能引用 ``NodeRegistry`` 已注册的
``node_type``；未注册动作不能被 Validator 放行，也不得在模型生成后自动注册。

M-08 只注册标准节点的契约（参数 schema、input/output、timeout、retry、风险、
幂等身份、可恢复边界、资源类）。真实 Activity 实现由 M-09～M-12 挂入
``app.plan.executors``；M-08 生产运行时对无实现的 Node 返回 NODE_EXECUTOR_UNAVAILABLE。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    SOURCE_SEARCH = "source_search"
    ACCESS_RULES_CHECK = "access_rules_check"
    LINK_DISCOVERY = "link_discovery"
    FETCH = "fetch"
    BROWSER_RENDER = "browser_render"
    EXTRACT = "extract"
    NORMALIZE = "normalize"
    DEDUPLICATE = "deduplicate"
    VALIDATE = "validate"
    GENERATE_ARTIFACT = "generate_artifact"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"


class ResourceClass(StrEnum):
    CORE = "core"
    HTTP = "http"
    BROWSER = "browser"
    LLM_SEARCH = "llm_search"


class ResourceKind(StrEnum):
    """Typed resource refs flowing between plan nodes (D-008)."""

    SPEC = "spec"
    SEED = "seed"
    CANDIDATE = "candidate"
    URL = "url"
    SNAPSHOT = "snapshot"
    RECORD = "record"
    EVIDENCE = "evidence"
    ARTIFACT = "artifact"
    BATCH = "batch"
    CREDENTIAL = "credential"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: int = 2


class _FetchParams(BaseModel):
    url_template: str
    max_redirects: int = 5
    render_if_empty: bool = False
    # 风险分级输入：非公开页面/使用网站凭据 → HIGH；绕过验证码 → PROHIBITED。
    # M-08 只保存契约；真实凭据访问执行在 M-10。
    non_public: bool = False
    credential_ref: str | None = None
    bypass_captcha: bool = False


class _SourceSearchParams(BaseModel):
    query: str
    max_results: int = 20
    locale: str | None = None


class _AccessRulesParams(BaseModel):
    respect_robots: bool = True
    public_only: bool = True


class _LinkDiscoveryParams(BaseModel):
    allow_outside_scope: bool = False
    max_links: int = 200


class _BrowserRenderParams(BaseModel):
    wait_selector: str | None = None
    full_page: bool = False


class _ExtractParams(BaseModel):
    fields: list[str]
    prefer_rules: bool = True


class _NormalizeParams(BaseModel):
    trim_whitespace: bool = True


class _DeduplicateParams(BaseModel):
    keys: list[str] = Field(default_factory=list)


class _ValidateParams(BaseModel):
    check_evidence: bool = True
    min_required_fields: int = 1


class _GenerateArtifactParams(BaseModel):
    format: str = "csv"
    dataset_version: str = "v1"


@dataclass(frozen=True)
class NodeDefinition:
    """Typed contract every standard node must declare (D-008)."""

    node_type: NodeType
    definition_version: str
    parameter_schema: type[BaseModel]
    input_contract: tuple[ResourceKind, ...]
    output_contract: tuple[ResourceKind, ...]
    timeout_seconds: int
    retry_policy: RetryPolicy
    risk_level: RiskLevel
    idempotency_identity: str
    recoverable_boundary: str
    resource_class: ResourceClass
    capability_metadata: dict[str, Any] = field(default_factory=dict)


_STANDARD_DEFINITIONS: tuple[NodeDefinition, ...] = (
    NodeDefinition(
        node_type=NodeType.SOURCE_SEARCH,
        definition_version="1.0.0",
        parameter_schema=_SourceSearchParams,
        input_contract=(ResourceKind.SPEC,),
        output_contract=(ResourceKind.CANDIDATE,),
        timeout_seconds=120,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.MEDIUM,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.LLM_SEARCH,
        capability_metadata={"provider": "search_provider", "requires": ["search_config"]},
    ),
    NodeDefinition(
        node_type=NodeType.ACCESS_RULES_CHECK,
        definition_version="1.0.0",
        parameter_schema=_AccessRulesParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC),
        output_contract=(ResourceKind.URL,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"robots": True},
    ),
    NodeDefinition(
        node_type=NodeType.LINK_DISCOVERY,
        definition_version="1.0.0",
        parameter_schema=_LinkDiscoveryParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC),
        output_contract=(ResourceKind.URL,),
        timeout_seconds=60,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
    ),
    NodeDefinition(
        node_type=NodeType.FETCH,
        definition_version="1.0.0",
        parameter_schema=_FetchParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC, ResourceKind.CREDENTIAL),
        output_contract=(ResourceKind.SNAPSHOT,),
        timeout_seconds=60,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.HTTP,
        capability_metadata={"tool_ladder": ["http", "browser"]},
    ),
    NodeDefinition(
        node_type=NodeType.BROWSER_RENDER,
        definition_version="1.0.0",
        parameter_schema=_BrowserRenderParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC, ResourceKind.CREDENTIAL),
        output_contract=(ResourceKind.SNAPSHOT,),
        timeout_seconds=180,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=5),
        risk_level=RiskLevel.MEDIUM,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.BROWSER,
        capability_metadata={"tool_ladder": ["browser"]},
    ),
    NodeDefinition(
        node_type=NodeType.EXTRACT,
        definition_version="1.0.0",
        parameter_schema=_ExtractParams,
        input_contract=(ResourceKind.SNAPSHOT, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD, ResourceKind.EVIDENCE),
        timeout_seconds=120,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"uses": ["rules", "llm_fallback"]},
    ),
    NodeDefinition(
        node_type=NodeType.NORMALIZE,
        definition_version="1.0.0",
        parameter_schema=_NormalizeParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
    ),
    NodeDefinition(
        node_type=NodeType.DEDUPLICATE,
        definition_version="1.0.0",
        parameter_schema=_DeduplicateParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
    ),
    NodeDefinition(
        node_type=NodeType.VALIDATE,
        definition_version="1.0.0",
        parameter_schema=_ValidateParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.EVIDENCE, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"partitions": ["passed", "needs_review", "rejected"]},
    ),
    NodeDefinition(
        node_type=NodeType.GENERATE_ARTIFACT,
        definition_version="1.0.0",
        parameter_schema=_GenerateArtifactParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.SPEC),
        output_contract=(ResourceKind.ARTIFACT,),
        timeout_seconds=60,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="artifact-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"formats": ["csv"]},
    ),
)


class NodeRegistry:
    """Code-registered static allowlist of standard nodes (D-008)."""

    def __init__(self, definitions: tuple[NodeDefinition, ...] = _STANDARD_DEFINITIONS) -> None:
        self._defs = {d.node_type: d for d in definitions}

    def register(self, definition: NodeDefinition) -> None:
        self._defs[definition.node_type] = definition

    def get(self, node_type: NodeType | str) -> NodeDefinition | None:
        if isinstance(node_type, str):
            try:
                node_type = NodeType(node_type)
            except ValueError:
                return None
        return self._defs.get(node_type)

    def all(self) -> list[NodeDefinition]:
        return list(self._defs.values())

    def is_registered(self, node_type: NodeType | str) -> bool:
        if isinstance(node_type, str):
            try:
                node_type = NodeType(node_type)
            except ValueError:
                return False
        return node_type in self._defs

    def planning_metadata(self) -> list[dict[str, Any]]:
        """LLM 可读的允许节点清单（不含 Secret，不含实现细节）。"""
        return [
            {
                "node_type": d.node_type.value,
                "risk_level": d.risk_level.value,
                "resource_class": d.resource_class.value,
                "input": [k.value for k in d.input_contract],
                "output": [k.value for k in d.output_contract],
                "timeout_seconds": d.timeout_seconds,
            }
            for d in self._defs.values()
        ]
