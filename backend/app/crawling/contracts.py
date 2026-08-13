"""M-10 typed Fetch 契约（D-008 typed contract）。

FetchRequest 是执行时构造的工作单元；FetchResult 是 Activity 统一返回契约。
原始正文只进 ObjectStorage，绝不进 Temporal Result / DomainEvent / SSE。
禁止在契约中携带 Cookie plaintext / username / password / Authorization / 完整 Credential。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.crawling.errors import FetchErrorCode

_STRICT = ConfigDict(extra="forbid")


class FetchTier(StrEnum):
    """能力阶梯（D-009 canonical）：STRUCTURED → STATIC → BATCH → BROWSER → BROWSER_AGENT。

    BROWSER_AGENT 本轮只保留契约，默认不启用自动执行。
    """

    STRUCTURED = "STRUCTURED"
    STATIC = "STATIC"
    BATCH = "BATCH"
    BROWSER = "BROWSER"
    BROWSER_AGENT = "BROWSER_AGENT"


class EscalationKind(StrEnum):
    """可验证升级证据类型（十九）：下游可传入/消费的 contract。"""

    EMPTY_BODY = "EMPTY_BODY"
    DYNAMIC_APP_SHELL = "DYNAMIC_APP_SHELL"
    JS_RENDER_SIGNAL = "JS_RENDER_SIGNAL"
    KEY_FIELDS_MISSING = "KEY_FIELDS_MISSING"
    INTERACTION_REQUIRED = "INTERACTION_REQUIRED"
    SITE_STRATEGY_BROWSER = "SITE_STRATEGY_BROWSER"


class EscalationEvidence(BaseModel):
    """升级必须有证据：无证据不得升级到 Playwright（二十）。"""

    model_config = _STRICT

    kind: EscalationKind
    detail: str = ""
    trigger_tool: str = ""
    observed_at: str | None = None  # ISO-8601


# 响应头 allowlist：安全记录摘要，绝不含 Set-Cookie/Authorization/Cookie/secret/session。
SAFE_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "etag",
        "last-modified",
        "cache-control",
        "expires",
        "server",
        "x-content-type-options",
        "date",
        "location",
    }
)


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """返回 allowlist 摘要；其余键一律丢弃（十四）。"""
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() in SAFE_RESPONSE_HEADERS}


class FetchRequest(BaseModel):
    """单 URL Fetch 工作单元（九）。request_metadata 仅允许安全字段。

    D-008 严格 typed contract：extra="forbid" 确保 Cookie/password/Authorization
    等未声明字段无法混入契约。
    """

    model_config = _STRICT

    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int = 0
    url_resource_id: int | None = None
    canonical_url: str
    url_hash: str
    access_decision: str = "ALLOW"  # M-09 AccessDecision 历史引用
    fetch_strategy_hint: FetchTier | None = None
    credential_ref: dict | None = None  # 脱敏 {credential_id,type,domain,scope}，无明文
    attempt_id: str = ""  # 幂等身份（node_type+input_fingerprint 派生）
    timeout_seconds: float = 30.0
    max_download_bytes: int = 5_000_000
    max_redirects: int = 5
    request_metadata: dict[str, Any] = Field(default_factory=dict)


class PageSnapshotRef(BaseModel):
    """M-11 Handoff：稳定指向 immutable stored content + fetch metadata + tool/hash。

    M-11 只消费 PageSnapshotRef，不重新请求当前网页（四十七）。
    """

    model_config = _STRICT

    snapshot_id: int
    content_hash: str
    storage_ref: str
    url: str
    final_url: str
    tool: str
    tool_version: str
    mime_type: str | None = None
    spec_version: int
    run_id: int
    url_resource_id: int | None = None
    fetched_at: str


class FetchResult(BaseModel):
    """统一 FetchResult（十）：status/tool/http/content/snapshot/escalation/retry/error。"""

    model_config = _STRICT

    status: str  # SUCCESS | FAILED | RETRY | SKIPPED | CREDENTIAL_REQUIRED | BROWSER_PENDING
    tool: str
    tool_version: str
    http_status: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    download_bytes: int | None = None
    duration_ms: int | None = None
    redirect_summary: list[dict] = Field(default_factory=list)
    snapshot_ref: PageSnapshotRef | None = None
    escalation_decision: bool = False
    escalation_evidence: EscalationEvidence | None = None
    retryable: bool = False
    site_strategy_result: dict | None = None
    error_code: FetchErrorCode | None = None
    error_summary: str = ""
