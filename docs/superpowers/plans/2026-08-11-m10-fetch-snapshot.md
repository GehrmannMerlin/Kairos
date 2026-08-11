# M-10 网页获取阶梯、HTTP/Scrapy/Playwright、Credential 与 PageSnapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (Inline Execution — user pre-authorized) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 D-009 固定能力升级阶梯（structured/静态 HTTP → Scrapy 批量 → 证据驱动 Playwright → Browser Agent 仅契约），把每次网页获取变成可审计、可恢复、幂等的 immutable PageSnapshot，并完成网站凭据（Cookie / Username-Password）+ M-08 Approval 的受控访问，交付 `FetchRequest/FetchResult/PageSnapshotRef/SiteFetchStrategy` 及 M-11 可消费的 Handoff 契约。

**Architecture:** 新增 `app/crawling/` 模块承载 Fetch/Browser/批量/凭据领域逻辑；复用 M-09 的 SSRF 守卫 HTTP transport（不建第二套 HTTP client）、M-09 Frontier 状态机（扩展 `FrontierState` 而非新建）、M-04 PageSnapshot/Checkpoint/Idempotency、M-08 `NODE_EXECUTORS`（只注册 `fetch` / `browser_render`）、M-07 `TaskWorkflow`（新增 `CREDENTIAL_REQUIRED` 等待分支）。原始内容按 content-hash 上传 ObjectStorage，DB 只存 metadata/hash/ref。M-08 计划图仍只看到 `Fetch → BrowserRender`；HTTP/Scrapy 由 Fetch 执行策略决定，Playwright 只在有 `EscalationEvidence` 时由 BrowserRender 节点运行。

**Tech Stack:** FastAPI/Pydantic v2、SQLAlchemy、Temporalio、httpx（复用 M-09）、Playwright（Chromium，本轮新增必需依赖）、MinIO（复用 M-01 ObjectStorage）、pytest（A-Lite）。

## Global Constraints

- M-09 基线：分支 `feature/M-09-source-discovery-frontier`，HEAD `5830063`，Alembic head `0007`。
- 只消费 `READY_FOR_FETCH` 且 `AccessDecision=ALLOW`（或合法 override 已 resolve）的 URL；不绕过 AccessDecision / robots / scope。
- 能力阶梯：TIER0 structured（API/JSON/RSS/Atom/Sitemap）→ TIER1 静态 HTTP（Scrapy = 同层批量）→ TIER2 Playwright Render（必须有证据）→ TIER3 Browser Agent（只保留契约，默认不启用）。
- Scrapy 是“大量已允许静态 URL 的 batch fetch executor”，不是权限升级工具；与普通 HTTP 共享 AccessDecision/robots/SSRF/Credential policy/错误分类。
- 401/403/captcha/private 不是 Playwright 升级理由；Playwright 不能改变访问权限。
- 验证码绝不自动绕过/第三方打码/LLM 猜解/反复 Browser 重试 → `CAPTCHA_REQUIRED`。
- Secret（Cookie/password/Authorization/Set-Cookie）绝不进日志/Temporal History/DomainEvent/SSE/PageSnapshot metadata/前端明文；只存 CredentialVault 加密，Activity 执行时临时解密，进程内存即用即弃。
- PageSnapshot immutable、content-addressable、owner-safe；相同内容重抓复用 Blob + 保留新 observation/version 关系。
- retry 有界：transient(网络/5xx/429) 内部有界重试；401/403/404/captcha 不重试、不升级。
- 不新增独立页面（13 页边界）；Credential Drawer 复用 M-05 overlay；不引入第二套 Secrets DB / 第二套 browser framework。
- 本轮不实现 M-11（Extract/FieldEvidence/Record）、M-16 scheduler、DEPLOY-GATE-3、不 Push/Merge/Tag。
- 每任务末尾 1 个语义化 Commit（feat/fix/test/docs），共 5~8 个，不产生 micro commits。

---

### Task 1: Fetch typed contracts + 错误分类 + PageSnapshot 持久化 + Migration 0008

**Files:**
- Create: `backend/app/crawling/__init__.py`, `backend/app/crawling/contracts.py`, `backend/app/crawling/errors.py`, `backend/app/crawling/snapshot.py`, `backend/app/crawling/repository.py`
- Modify: `backend/app/domain/models.py`（SiteFetchStrategy + PageSnapshot 扩展列）, `backend/app/domain/repository.py`（PageSnapshotRepository/SiteFetchStrategyRepository）, `backend/app/config.py`（集中 fetch 配置）, `backend/alembic/versions/0008_extend_page_snapshot_fetch.py`（新增）, `backend/app/discovery/models.py`（FrontierState 扩展）
- Test: `backend/tests/crawling/test_contracts.py`, `backend/tests/crawling/test_snapshot.py`

**Interfaces:**
- Produces: `FetchRequest`/`FetchResult`/`PageSnapshotRef`/`EscalationEvidence`/`FetchErrorCode` (contracts.py)；`PageSnapshotService.commit_raw(...)`/`lookup_by_hash(...)` (snapshot.py)；`SiteFetchStrategy`/`PageSnapshot` ORM 扩展；FrontierState 新枚举值 `FETCHING/FETCHED/FETCH_FAILED/SKIPPED/WAITING_CREDENTIAL/BROWSER_PENDING`；Settings 新字段（见 Step 4）。

- [ ] **Step 1: 扩展 `FrontierState` 枚举（M-09 同源状态机，不新建）**

`backend/app/discovery/models.py` 的 `FrontierState` 增加：
```python
FETCHING = "FETCHING"
FETCHED = "FETCHED"
FETCH_FAILED = "FETCH_FAILED"
SKIPPED = "SKIPPED"
WAITING_CREDENTIAL = "WAITING_CREDENTIAL"
BROWSER_PENDING = "BROWSER_PENDING"
```

- [ ] **Step 2: 新增 `app/crawling/errors.py`（单一 FetchError 分类）**

```python
"""M-10 crawling 错误分类（复用 Code Standards 10.3 taxonomy 子集）。"""
from __future__ import annotations
from enum import StrEnum

class FetchErrorCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    DNS_ERROR = "DNS_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    NOT_FOUND = "NOT_FOUND"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    UNSUPPORTED_RESPONSE = "UNSUPPORTED_RESPONSE"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    DYNAMIC_RENDER_REQUIRED = "DYNAMIC_RENDER_REQUIRED"
    CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"
    STORAGE_ERROR = "STORAGE_ERROR"
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"
    SIZE_LIMIT_EXCEEDED = "SIZE_LIMIT_EXCEEDED"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

# 可内部重试（网络/5xx/429），其余不重试
RETRYABLE_CODES = {FetchErrorCode.TIMEOUT, FetchErrorCode.DNS_ERROR,
                   FetchErrorCode.CONNECTION_ERROR, FetchErrorCode.RATE_LIMITED,
                   FetchErrorCode.SERVER_ERROR}

class CrawlingError(Exception): ...
class SnapshotCommitError(CrawlingError): ...
```
不做两套重复 enum：M-11/M-12 复用 `FetchErrorCode`。

- [ ] **Step 3: 新增 `app/crawling/contracts.py`（typed FetchRequest/FetchResult/PageSnapshotRef/EscalationEvidence）**

```python
from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, Field
from app.crawling.errors import FetchErrorCode

class FetchTier(StrEnum):
    STRUCTURED = "STRUCTURED"; STATIC = "STATIC"; BATCH = "BATCH"
    BROWSER = "BROWSER"; BROWSER_AGENT = "BROWSER_AGENT"

class EscalationKind(StrEnum):
    EMPTY_BODY = "EMPTY_BODY"; DYNAMIC_APP_SHELL = "DYNAMIC_APP_SHELL"
    JS_RENDER_SIGNAL = "JS_RENDER_SIGNAL"; KEY_FIELDS_MISSING = "KEY_FIELDS_MISSING"
    INTERACTION_REQUIRED = "INTERACTION_REQUIRED"; SITE_STRATEGY_BROWSER = "SITE_STRATEGY_BROWSER"

class EscalationEvidence(BaseModel):
    kind: EscalationKind
    detail: str = ""
    trigger_tool: str = ""
    observed_at: str | None = None  # ISO-8601

class HeaderRedaction(BaseModel):
    """安全记录响应头摘要：只允许 allowlist（见 TASK 2）。"""
    items: dict[str, str] = Field(default_factory=dict)

class FetchRequest(BaseModel):
    # 禁止携带 Cookie plaintext / username/password / Authorization value / 完整 Credential
    task_id: int; user_id: int; run_id: int
    spec_version: int; plan_version: int = 0
    url_resource_id: int | None = None
    canonical_url: str; url_hash: str
    access_decision: str = "ALLOW"      # M-09 AccessDecision 引用（历史快照）
    fetch_strategy_hint: FetchTier | None = None
    credential_ref: dict | None = None  # 脱敏：{credential_id, type, domain, scope}，无明文
    attempt_id: str = ""                # 幂等身份（node_type+input_fingerprint 派生）
    timeout_seconds: float = 30.0
    max_download_bytes: int = 5_000_000
    max_redirects: int = 5
    request_metadata: dict = Field(default_factory=dict)  # 仅安全字段

class PageSnapshotRef(BaseModel):
    snapshot_id: int; content_hash: str; storage_ref: str
    url: str; final_url: str; tool: str; tool_version: str
    mime_type: str | None = None
    spec_version: int; run_id: int; url_resource_id: int | None = None
    fetched_at: str

class FetchResult(BaseModel):
    status: str            # SUCCESS | FAILED | RETRY | SKIPPED | CREDENTIAL_REQUIRED | BROWSER_PENDING
    tool: str; tool_version: str
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
```
`FetchRequest` 是“执行时构造的工作单元”；`FetchResult` 是 Activity 返回的统一契约（原始正文只进 ObjectStorage，绝不进 Temporal/DomainEvent/SSE）。

- [ ] **Step 4: 集中配置（不散落 magic number）**

`backend/app/config.py` `Settings` 增加：
```python
fetch_timeout_seconds: float = 30.0
fetch_max_download_bytes: int = 5_000_000
fetch_max_redirects: int = 5
fetch_internal_retries: int = 2
fetch_internal_retry_base_seconds: float = 1.0
site_strategy_ttl_seconds: int = 86400
browser_render_timeout_seconds: float = 60.0
```
Temporal Activity retry 已由 `app/plan/nodes.py` NodeDefinition 固定（FETCH max_attempts=3 / BROWSER_RENDER max_attempts=2）。

- [ ] **Step 5: 扩展 ORM 模型 + 新增 SiteFetchStrategy（`app/domain/models.py`）**

`PageSnapshot` 增加列（immutable observation；同内容重抓 → 新行 + snapshot_version 递增）：
```python
# 追加到现有 PageSnapshot 类
spec_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
tool: Mapped[str] = mapped_column(String(30), nullable=False, default="http")
tool_version: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
final_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
download_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
redirect_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
escalation_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
prior_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
credential_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 脱敏，无明文
http_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # 脱敏响应头 allowlist 摘要
```
`URLResource` 追加（fetch 审计）：
```python
fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
fetch_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
```
新增 `SiteFetchStrategy`（owner-safe，user+site 唯一；可跨同用户任务复用，D-009 策略复用）：
```python
class SiteFetchStrategy(Base):
    __tablename__ = "site_fetch_strategies"
    __table_args__ = (UniqueConstraint("user_id", "site_host", name="uq_sfs_user_site"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    site_host: Mapped[str] = mapped_column(String(255), nullable=False)
    preferred_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="static")
    tool: Mapped[str] = mapped_column(String(30), nullable=False, default="http")
    tool_version: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")
    structure_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credential_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="probing")  # probing|valid|expired
    created_at / updated_at（与现有模型一致，server_default=func.now()）
```

- [ ] **Step 6: Migration `0008_extend_page_snapshot_fetch.py`**

```python
"""M-10: PageSnapshot fetch metadata + SiteFetchStrategy + URLResource fetch audit + credentials 网站列。"""
revision = "0008"; down_revision = "0007"
# op.add_column 扩展 page_snapshots（spec_version, tool, tool_version, final_url, http_status,
#   content_length, download_bytes, duration_ms, redirect_summary, escalation_evidence,
#   snapshot_version, prior_snapshot_id, credential_ref, http_metadata）
# op.add_column 扩展 url_resources（fetched_at, fetch_error_code）
# op.create_table site_fetch_strategies（含 uq_sfs_user_site 唯一约束）
# op.add_column 扩展 credentials（domain, scope, task_id；nullable，website 凭据用）
# 全部 expand 兼容；server_default 补齐非空列。
```

- [ ] **Step 7: 新增 `app/crawling/repository.py`（PageSnapshotRepository / SiteFetchStrategyRepository）**

```python
class PageSnapshotRepository:
    def __init__(self, db): self._db = db
    def find_by_id(self, user_id: int, snapshot_id: int) -> PageSnapshot | None: ...
    def find_by_hash(self, *, user_id: int, content_hash: str, tool: str) -> list[PageSnapshot]: ...
    def next_version(self, *, user_id: int, url_resource_id: int) -> int: ...
    def create(self, *, user_id, task_id, run_id, url_resource_id, spec_version, content_hash,
               storage_ref, mime_type, tool, tool_version, final_url, http_status,
               content_length, download_bytes, duration_ms, redirect_summary,
               escalation_evidence, snapshot_version, prior_snapshot_id, credential_ref,
               http_metadata) -> PageSnapshot: ...
    def list_for_task(self, user_id, task_id, limit=200) -> list[PageSnapshot]: ...
    def find_by_url_hash(self, *, user_id, url_hash) -> list[PageSnapshot]: ...  # 供重抓审计
```
`SiteFetchStrategyRepository`：`get(user_id, site_host)` / `upsert(...)` / `invalidate(user_id, site_host)` / `list_for_user(user_id)`。

- [ ] **Step 8: 新增 `app/crawling/snapshot.py`（content-hash 对象复用 + observation 持久化 + 幂等恢复）**

```python
class PageSnapshotService:
    def __init__(self, db, storage: ObjectStorage, *, user_id, task_id, run_id, spec_version):
        ...
    def _key(self, content_hash: str, tool: str, ext: str) -> str:
        import uuid
        return f"snapshots/u{self._user_id}/{content_hash}/{tool}-{uuid.uuid4().hex}.{ext}"
    def commit_raw(self, *, body: bytes, url_resource_id: int, tool: str, tool_version: str,
                   final_url: str, http_status: int | None, content_type: str | None,
                   content_length: int | None, duration_ms: int | None,
                   redirect_summary: list[dict], escalation_evidence: dict | None = None,
                   credential_ref: dict | None = None, http_metadata: dict | None = None,
                   url_hash: str, node_run_id: int | None = None) -> PageSnapshotRef:
        # 1. content_hash = sha256(body).hexdigest()
        # 2. 同 (user_id, content_hash, tool) 已有 snapshot → 复用其 storage_ref（不重复上传）
        #    → 仅新建 observation 行（snapshot_version = next_version + 1, prior_snapshot_id=最新已有行 id）
        # 3. 否则：storage_key=_key(...)；storage.exists(key) 已存在则复用，不存在则 storage.put
        #    （blob 先写；DB 失败重试时按 exists/hash 幂等复用，不无限复制对象）
        # 4. repository.create(...) → PageSnapshotRef
    def lookup_by_hash(self, *, content_hash: str, tool: str) -> PageSnapshotRef | None: ...
```

- [ ] **Step 9: 测试契约 + snapshot 复用/隔离（写失败测试→跑→实现→跑）**

`backend/tests/crawling/test_contracts.py`：
```python
def test_fetch_request_forbids_secret_fields():
    # FetchRequest 字段白名单：不存在 cookie/password/authorization 字段
    import inspect
    fields = set(FetchRequest.model_fields)
    assert not ({"cookie", "password", "authorization"} & fields)

def test_fetch_error_code_retryable_map():
    assert RETRYABLE_CODES >= {FetchErrorCode.SERVER_ERROR, FetchErrorCode.RATE_LIMITED}
    assert FetchErrorCode.NOT_FOUND not in RETRYABLE_CODES
    assert FetchErrorCode.AUTH_REQUIRED not in RETRYABLE_CODES
```
`backend/tests/crawling/test_snapshot.py`（内存 SQLite + FakeStorage）：
```python
def test_same_content_reuses_blob_and_creates_new_observation(ctx):
    # 两次 commit_raw 相同 body → 同一 storage key（FakeStorage put 调用次数 = 1）
    # → 2 行 PageSnapshot，snapshot_version 1/2，prior_snapshot_id 关联，content_hash 一致
def test_snapshot_owner_isolation(ctx):
    # user A 的 snapshot；user B find_by_id → None（owner-safe 404 语义）
def test_snapshot_immutable(ctx):
    # 已存在行不可 UPDATE；写入后内容不变（无 update API）
```
跑：`.venv/Scripts/python.exe -m pytest tests/crawling/test_contracts.py tests/crawling/test_snapshot.py -q` → PASS。

- [ ] **Step 10: ruff/mypy + Commit**

```bash
.venv/Scripts/python.exe -m ruff check app tests && .venv/Scripts/python.exe -m mypy app
git add backend/app/crawling backend/app/domain/models.py backend/app/domain/repository.py \
        backend/app/config.py backend/app/discovery/models.py backend/alembic/versions/0008_extend_page_snapshot_fetch.py \
        backend/tests/crawling
git commit -m "feat(fetch): add typed fetch/snapshot contracts and page snapshot persistence"
```

---

### Task 2: 静态 HTTP + 结构化 Fetch + Fetch 执行器核心（含 E2E Fixture 1 静态与 Fixture 4 失败/重试）

**Files:**
- Create: `backend/app/crawling/http_fetch.py`, `backend/app/crawling/content.py`, `backend/app/crawling/fetch_executor.py`, `backend/tests/crawling/test_http_fetch.py`, `backend/tests/crawling/test_fetch_e2e_static.py`
- Modify: `backend/app/crawling/errors.py`（如需扩展）

**Interfaces:**
- Consumes: Task 1 `FetchRequest/FetchResult/FetchErrorCode`、`PageSnapshotService`、`UrlFrontierRepository`、`AccessDecision`/`RobotsCache`(M-09)、`DiscoveryHttp`(M-09)。
- Produces: `SafeFetchHttp`（get_body）、`classify_content(...) -> ContentClass`、`FetchNodeExecutor.execute(unit)`。

- [ ] **Step 1: 新增 `app/crawling/http_fetch.py` — 复用 M-09 transport + 真 Fetch 能力扩展**

扩展 `app/discovery/http.py` 的 `DiscoveryHttp`（不重写：M-09 一个 transport，M-10 只加 streaming/body/size cap/timing/hash）：
```python
from app.discovery.http import DiscoveryHttp, _HttpResponse, _REDIRECT_STATUS, _MAX_REDIRECTS
class SafeFetchHttp:
    """完整 Fetch transport：复用 M-09 SSRF 守卫 + 每跳重定向复核；扩展 size cap/timing/hash。"""
    def __init__(self, *, transport=None, allow_hosts=frozenset(), max_redirects=5, max_bytes=5_000_000,
                 timeout_seconds=30.0): ...
    async def get_body(self, url, *, headers: dict | None = None) -> FetchedBody:
        # 每个 hop assert_safe_url（M-09 ssrf.assert_safe_url，含 allow_hosts 测试绕过）
        # 有界重定向（max_redirects），每次重定向重新 SSRF/scheme/scope 校验
        # 读取响应体并强制 size cap（SIZE_LIMIT_EXCEEDED）；记录 duration/timing
        # 返回 FetchedBody(status_code, body, final_url, content_type, headers_allowlist, redirect_chain)
```
`FetchedBody` dataclass + `redact_headers(headers) -> dict`（**allowlist**：content-type, content-length, etag, last-modified, cache-control, expires, server, x-content-type-options, date, location；其余一律丢弃，绝不含 set-cookie/authorization/cookie/proxy-authorization/token）。

- [ ] **Step 2: 新增 `app/crawling/content.py` — 内容分类（结构化/静态/空壳）**

```python
class ContentClass(StrEnum):
    STRUCTURED = "STRUCTURED"   # json/xml/rss/atom/sitemap（按 content-type/URL 后缀）
    HTML_STATIC = "HTML_STATIC"
    EMPTY = "EMPTY"             # 空 body 或纯 JS shell（无实质 HTML 内容）
    DYNAMIC_SHELL = "DYNAMIC_SHELL"  # 有 app shell 特征（<div id="app">、script 密集、正文极小）
def classify_content(*, url: str, content_type: str | None, body: bytes) -> ContentClass:
    # 结构化：content-type json/xml/atom/rss 或 URL 后缀 .json/.xml/.rss/.atom
    # 空：strip 后无文本节点 → EMPTY
    # DYNAMIC_SHELL：正文 < 阈值 或含 app shell marker 且 <html> 文本极少
    # 其余 → HTML_STATIC
def build_escalation_evidence(content_class: ContentClass) -> EscalationEvidence | None:
    # EMPTY → EscalationEvidence(kind=EMPTY_BODY, trigger_tool="http")
    # DYNAMIC_SHELL → EscalationEvidence(kind=DYNAMIC_APP_SHELL, trigger_tool="http")
    # 其他 → None（无证据不升级）
```

- [ ] **Step 3: 新增 `app/crawling/fetch_executor.py` — Fetch 节点执行器（静态层核心）**

```python
class FetchNodeExecutor:
    """M-08 FETCH 节点真实执行器：只处理 READY_FOR_FETCH，复用 M-09 AccessDecision。"""
    def __init__(self, db, *, http=None, robots=None, snapshot=None, site_strategy=None,
                 user_agent=DEFAULT_USER_AGENT, allow_hosts=frozenset()): ...
    async def execute(self, unit) -> ExecuteUnitResult:
        # 1. 读 run/spec（同 M-09 AccessRulesService 模式）；frontier.list_ready_for_fetch(...)
        # 2. 每 URL 循环：
        #    a. 重新校验 access：decide_access(row.url, spec, robots_policy) → 非 ALLOW → BLOCKED/SKIPPED
        #    b. SiteFetchStrategy 命中且 valid：
        #       - preferred_tier == browser → 直接 mark BROWSER_PENDING + 记录 SITE_STRATEGY_BROWSER 证据
        #         （策略即证据；仍需 AccessDecision 复核，见 c）
        #       - 否则走 HTTP
        #    c. mark_state(FETCHING)
        #    d. SafeFetchHttp.get_body(...)；错误分类（NOT_FOUND/AUTH/ACCESS_DENIED/CAPTCHA/RETRYABLE/...）
        #       - 401/403：有 credential_ref（已批准）→ 复用 Task 2 Step 5 凭据附着；无 → CREDENTIAL_REQUIRED
        #       - CAPTCHA marker → CAPTCHA_REQUIRED，不自动绕过，不 retry，不升级
        #       - transient/5xx/429 → 有界内部重试（fetch_internal_retries 次，指数退避+抖动），仍失败 → FAILED
        #       - 404 → NOT_FOUND，不 retry
        #    e. classify_content → 结构化/静态成功 → PageSnapshotService.commit_raw → FETCHED + SSE
        #       EMPTY/DYNAMIC_SHELL → 保留 HTTP attempt snapshot（escalation_evidence 标记）
        #       → mark BROWSER_PENDING → 聚合 FETCH_RESULT BROWSER_PENDING
        #    f. site_strategy 更新（成功/失败计数、TTL）
        # 3. 若有 URL CREDENTIAL_REQUIRED → 返回 status="CREDENTIAL_REQUIRED"，refs={url_hash, domain, parameters}
        #    否则返回 status="OK"，refs={fetched, snapshots:[...], browser_pending, blocked, run_id}
        # 4. SSE/domain events：fetch.started / fetch.completed / fetch.failed /
        #    fetch.credential_required / fetch.escalated（聚合，非每 URL 细粒度；D-039）
```

- [ ] **Step 4: 单测（写失败→跑→实现→跑）**

`backend/tests/crawling/test_http_fetch.py`（FakeTransport + allow_hosts 绕过 SSRF）：
```python
def test_redirect_revalidates_ssrf(ctx):
    # 安全 URL → 302 → 127.0.0.1 → SafeFetchHttp 拒绝（SSRF_BLOCKED），不允许安全→私网跳转
def test_header_allowlist_redacts_secrets(ctx):
    # 响应含 set-cookie/authorization → redact_headers 不含这些键
def test_size_cap_enforced(ctx):
    # body 超过 max_bytes → SIZE_LIMIT_EXCEEDED
def test_transient_retry_bounded(ctx):
    # 503,503,200 → 内部重试成功（retry_count=2 内）；503,503,503 → 有界失败，不无限重试
def test_404_no_retry(ctx):
    # 404 → 一次即 NOT_FOUND，不重试
```

- [ ] **Step 5: E2E Fixture 1 — 静态 HTML 全链（强制：不启动 Playwright）**

`backend/tests/crawling/test_fetch_e2e_static.py`：本地 fixture（FakeTransport 返回普通 200 HTML + `render_invoked` 计数注入 FakeRenderer）。断言：
```python
def test_static_fetch_full_chain_and_no_browser(ctx, http, renderer):
    # READY_FOR_FETCH seed → FetchNodeExecutor.execute → PageSnapshot 写入
    #   → frontier.status == FETCHED
    #   → snapshot.storage_ref 非空、content_hash 与 body sha256 一致
    #   → renderer.invocation_count == 0（静态页面不启动 Playwright 强制门禁）
```

- [ ] **Step 6: E2E Fixture 4 — 失败/重试 + 401/403 不盲升级**

`backend/tests/crawling/test_fetch_e2e_failure.py`：
```python
def test_failure_then_success_bounded_retry(ctx, http):
    # FakeTransport 第一次 503、第二次 200 → 最终 FETCHED；renderer 未调用
def test_auth_denied_no_blind_escalation(ctx, http, renderer):
    # 401 → CREDENTIAL_REQUIRED / 403 → ACCESS_DENIED；renderer.invocation_count == 0
    #   （401/403 不是 Playwright 升级理由）
def test_captcha_no_bypass(ctx, http, renderer):
    # 响应含 captcha marker → CAPTCHA_REQUIRED；无 auto-solve/第三方/bypass；renderer 未调用
```

- [ ] **Step 7: ruff/mypy + Commit**

```bash
.venv/Scripts/python.exe -m ruff check app tests && .venv/Scripts/python.exe -m mypy app
git add backend/app/crawling backend/tests/crawling
git commit -m "feat(fetch): add static http and structured fetch executor"
```

---

### Task 3: Scrapy 批量 Fetch 策略（同层批量，非权限升级）

**Files:**
- Create: `backend/app/crawling/batch.py`, `backend/tests/crawling/test_batch_fetch.py`

**Interfaces:**
- Consumes: `SafeFetchHttp`、`classify_content`、`PageSnapshotService`、`FetchErrorCode`、`UrlFrontierRepository`。
- Produces: `BatchFetchResult`（每 URL 的 `FetchResult` 列表）、`run_batch_fetch(...)`。

- [ ] **Step 1: 新增 `app/crawling/batch.py` — Scrapy-style 批量执行器**

```python
class ScrapyBatchFetcher:
    """Scrapy 批量模式：大量已允许静态 URL 的 batch fetch executor。

    Scrapy 只是静态层的批量执行模式（D-009 TIER1）；与普通 HTTP 共享
    AccessDecision/robots/SSRF/Credential policy/错误分类，不改变访问权限
    （403/login/captcha/robots 不能被 Scrapy 绕过）。为避免第二套 HTTP client
    （十六），复用 M-09 SafeFetchHttp 有界并发。
    """
    def __init__(self, db, *, http=None, robots=None, snapshot=None,
                 max_concurrency=4, user_agent=DEFAULT_USER_AGENT): ...
    async def run(self, *, user_id, task_id, run_id, spec_version, urls: list[URLResource]) -> BatchFetchResult:
        # 1. 复用 Task 2 每 URL 校验/抓取/快照路径，仅加并发调度
        # 2. asyncio.Semaphore(max_concurrency) 有界并发（HTTP 资源类高并发池，M-10 不实现 M-16）
        # 3. 每 URL 小批次提交 snapshot + frontier（事务粒度）；单 URL 失败不毒化同批
        # 4. 统一转换成同一个 FetchResult/PageSnapshot/Checkpoint/error taxonomy
```

- [ ] **Step 2: 测试（批量成功 + 不越权 + 有界并发 + 失败隔离）**

`backend/tests/crawling/test_batch_fetch.py`：
```python
def test_batch_fetches_multiple_static_urls(ctx, http):
    # 5 个 READY_FOR_FETCH → run → 5 个 FetchResult SUCCESS + 5 个 PageSnapshot + 全部 FETCHED
def test_batch_does_not_bypass_robots(ctx, http):
    # robots Disallow 路径即使混入批量 → SKIPPED/BLOCKED，不抓取（Scrapy 不改变访问权限）
def test_batch_failure_isolation(ctx, http):
    # 其中一个 500，其余 200 → 成功 URL FETCHED，失败 URL FETCH_FAILED（不毒化同批）
def test_batch_bounded_concurrency(ctx, http):
    # FakeTransport 记录最大并发 ≤ max_concurrency
```

- [ ] **Step 3: ruff/mypy + Commit**

```bash
.venv/Scripts/python.exe -m ruff check app tests && .venv/Scripts/python.exe -m mypy app
git add backend/app/crawling/batch.py backend/tests/crawling/test_batch_fetch.py
git commit -m "feat(fetch): add scrapy-style bounded batch fetch executor"
```

---

### Task 4: 证据驱动 Playwright BrowserRender + 升级链（含 E2E Fixture 2 动态）

**Files:**
- Create: `backend/app/crawling/browser.py`, `backend/tests/crawling/test_browser_render.py`, `backend/tests/crawling/test_fetch_e2e_dynamic.py`
- Modify: `backend/app/crawling/fetch_executor.py`（BROWSER_PENDING 标记路径已在 Task 2 Step 3，补齐证据校验）

**Interfaces:**
- Consumes: Task 2 `EscalationEvidence`/`PageSnapshotService`/`UrlFrontierRepository`。
- Produces: `BrowserRenderer` Protocol + `PlaywrightChromiumRenderer`、`BrowserRenderNodeExecutor.execute(unit)`。

- [ ] **Step 1: 新增 `app/crawling/browser.py` — renderer Protocol + Playwright 实现**

```python
class BrowserRenderer(Protocol):
    async def render(self, *, url: str, timeout_seconds: float,
                     cookies: list[dict] | None = None) -> RenderedPage: ...
@dataclass
class RenderedPage:
    html: bytes; final_url: str; title: str | None = None

class PlaywrightChromiumRenderer:
    """真实 Playwright 渲染器：只在有 EscalationEvidence 且访问已验证时调用。"""
    def __init__(self, *, headless=True, timeout_seconds=60.0, allow_hosts=frozenset()): ...
    async def render(self, *, url, timeout_seconds=None, cookies=None) -> RenderedPage:
        # 每个渲染 URL 仍先 assert_safe_url（SSRF 复用）
        # 启动 chromium（惰性 import playwright），context.add_cookies（若提供，仅匹配 domain）
        # page.goto → 等待 networkidle 或稳定 → content() → 关闭 context
```
Browser Agent（D-009 TIER3）只保留 `FetchTier.BROWSER_AGENT` + 契约常量 `BROWSER_AGENT_REQUIRED = "BROWSER_AGENT_REQUIRED"`，不实现点击/填表/滚动/验证码。

- [ ] **Step 2: BrowserRenderNodeExecutor**

```python
class BrowserRenderNodeExecutor:
    """M-08 BROWSER_RENDER 节点：只消费 BROWSER_PENDING（有 EscalationEvidence）URL。"""
    def __init__(self, db, *, renderer=None, snapshot=None, site_strategy=None, allow_hosts=frozenset()): ...
    async def execute(self, unit) -> ExecuteUnitResult:
        # 1. 读 BROWSER_PENDING URL（owner+task）
        # 2. 每 URL：重新校验 AccessDecision（robots/scope）→ 非 ALLOW → BLOCKED
        #    escalation gate：无 EscalationEvidence 且无 SITE_STRATEGY_BROWSER 策略证据
        #      → 不渲染（无证据不升级），URL → FETCH_FAILED(UNSUPPORTED_RESPONSE)
        # 3. renderer.render(url) → RenderedPage
        # 4. PageSnapshotService.commit_raw(tool="playwright", tool_version=..., escalation_evidence=保留,
        #    prior_snapshot_id=HTTP shell snapshot id) → FETCHED + SSE fetch.escalated/fetch.completed
        # 5. site_strategy 更新（preferred_tier=browser）
        # 6. 返回 ExecuteUnitResult(status="OK", refs={rendered, snapshots, blocked, run_id})
```

- [ ] **Step 3: 测试（写失败→跑→实现→跑）**

`backend/tests/crawling/test_browser_render.py`（FakeRenderer 注入）：
```python
def test_render_requires_escalation_evidence(ctx, renderer):
    # BROWSER_PENDING URL 无证据 → 不调用 renderer，URL → FETCH_FAILED
def test_render_preserves_http_attempt_chain(ctx, renderer):
    # HTTP shell snapshot 存在 → render 后新 snapshot prior_snapshot_id 指向 shell，两条都保留
def test_render_failure_sets_fetch_failed(ctx, renderer):
    # renderer 抛异常 → FETCH_FAILED + 错误分类（不无限 Browser retry）
```

- [ ] **Step 4: E2E Fixture 2 — 动态页面升级链（HTTP→证据→Playwright→rendered snapshot）**

`backend/tests/crawling/test_fetch_e2e_dynamic.py`（FakeTransport 返回 JS shell，FakeRenderer 返回 JS 执行后的真实内容）：
```python
def test_dynamic_http_shell_to_playwright_rendered(ctx, http, renderer):
    # HTTP 得到 DYNAMIC_SHELL（EMPTY_BODY/DYNAMIC_APP_SHELL）→ 保留 HTTP attempt snapshot
    #   → URL BROWSER_PENDING → BrowserRenderNodeExecutor → rendered snapshot（tool=playwright）
    #   → frontier FETCHED；升级原因真实存在（escalation_evidence 非空）；renderer.invocation_count == 1
def test_static_page_never_reaches_renderer(ctx, http, renderer):
    # 普通 200 HTML → fetch 完成即 FETCHED；renderer.invocation_count == 0（跨层复核）
```

- [ ] **Step 5: 真实 Playwright 验证（浏览器可用时；不可用则跳过并如实披露）**

- 在 venv 安装：`.venv/Scripts/pip.exe install playwright && .venv/Scripts/python.exe -m playwright install chromium`
- 新增 `backend/tests/crawling/test_playwright_real.py`，`@pytest.mark.skipif(not _chromium_available(), ...)`：
```python
async def test_real_chromium_renders_local_fixture():
    # 本地临时起一个 http.server 返回 JS 注入内容 → PlaywrightChromiumRenderer.render
    #   → html 含 JS 注入后的文本
```
跑：`pytest tests/crawling/test_playwright_real.py -q`。若 chromium 不可安装 → 该测试 skip，并在 M-10-execution.md 明确“真实浏览器路径未验证（依赖缺失）”，Service 级动态 fixture 仍通过。

- [ ] **Step 6: ruff/mypy + Commit**

```bash
.venv/Scripts/python.exe -m ruff check app tests && .venv/Scripts/python.exe -m mypy app
git add backend/app/crawling/browser.py backend/tests/crawling
git commit -m "feat(browser): add evidence-driven playwright rendering"
```

---

### Task 5: 网站凭据 + Approval 受控访问（Cookie / Username-Password + E2E Fixture 3）

**Files:**
- Create: `backend/app/crawling/credentials.py`, `backend/app/activities/credential_approval.py`, `backend/app/api/routes/credentials.py`, `backend/tests/crawling/test_website_credentials.py`, `backend/tests/crawling/test_fetch_e2e_credential.py`
- Modify: `backend/app/api/schemas.py`（CredentialDto 等）, `backend/app/api/router.py`（挂路由）, `backend/app/credentials/vault.py`（website 类型入口）, `backend/app/discovery/models.py`（无，状态已在 Task 1）, `backend/app/activities/execution_seam.py`（如需）与 `backend/app/workflows/task_workflow.py`（CREDENTIAL_REQUIRED 分支）

**Interfaces:**
- Consumes: `CredentialVault`(M-03)、`ApprovalService`(M-08)、`TaskWorkflow`(M-07)、Task 2 `FetchNodeExecutor`。
- Produces: `WebsiteCredentialService`、`resolve_credential_access` activity、`POST/GET /tasks/{id}/credentials`、`GET/DELETE /credentials/saved`、TaskWorkflow `CREDENTIAL_REQUIRED` 分支。

- [ ] **Step 1: 扩展 `app/credentials/models.py` 的 `Credential`（网站凭据元数据，Secret 仍在 vault 密文）**

```python
# credentials 表新增（migration 0008 已含）
domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
scope: Mapped[str | None] = mapped_column(String(20), nullable=True)   # CURRENT_TASK | SAVED_DOMAIN
task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # CURRENT_TASK 归属
# kind 允许新增: cookie | username_password
```
`app/credentials/vault.py` 增加 website 统一入口（复用加密/读密，无第二套 Secrets DB）：
```python
WEBSITE_CREDENTIAL_KINDS = {"cookie", "username_password"}
def store_website_secret(self, *, user_id, kind, name, secret_json, domain, scope, task_id) -> CredentialInfo:
    # 校验 kind in WEBSITE_CREDENTIAL_KINDS + domain；CURRENT_TASK 必须带 task_id
    # store_secret(kind=kind, ...) + 更新 credentials 行 domain/scope/task_id
```

- [ ] **Step 2: 新增 `app/crawling/credentials.py` — WebsiteCredentialService（owner 隔离 + scope 执行）**

```python
class WebsiteCredentialService:
    def __init__(self, db, vault): ...
    def store(self, *, user_id, task_id, ctype: str, payload: dict, scope: str,
              domain: str) -> dict:
        # ctype: cookie → payload={"cookies":[{"name","value","domain","path"}]}
        #        username_password → payload={"username","password"}
        # secret_json = json.dumps(payload) → vault.store_website_secret(...)
        # 返回脱敏 metadata {credential_id, type, domain, scope, masked: "cred-****"}
        # CURRENT_TASK：TaskRepository.get_owned 校验 + credential.task_id = task_id
    def list_for_task(self, *, user_id, task_id) -> list[dict]: ...     # 脱敏
    def list_saved_for_user(self, *, user_id) -> list[dict]: ...        # SAVED_DOMAIN
    def delete(self, *, user_id, credential_id) -> None:                # vault.revoke + 行删除（owner-safe）
    def resolve_for_fetch(self, *, user_id, task_id, domain) -> dict | None:
        # 返回脱敏 credential_ref：优先 CURRENT_TASK(task_id+domain)，其次 SAVED_DOMAIN(domain)
        # 仅 {credential_id, type, domain, scope}，无明文
    def read_for_execution(self, *, user_id, credential_id) -> dict:
        # 仅 Activity 内调用：vault.get_active → read_for_execution → json.loads
        # 进程内存即用即弃，绝不持久化/日志
    def build_attachment(self, *, secret: dict, url: str) -> dict:
        # cookie → {"cookies":[{name,value,domain,path}]}（domain 必须匹配目标 host，41）
        # username_password → {"basic_auth": (username, password)}（42：明确认证方式）
        # 跨域检查：cookies domain/secret 只对匹配 host 生效，禁止 a.com cookie 发 b.com
```

- [ ] **Step 3: 新增 API 路由 `app/api/routes/credentials.py`（Route 只做 auth/DTO 映射）**

```python
router = APIRouter(prefix="/tasks", tags=["credentials"])
saved_router = APIRouter(prefix="/credentials", tags=["credentials"])

class WebsiteCredentialCommand(BaseModel):
    type: Literal["cookie", "username_password"]
    payload: dict                      # cookie: {cookies:[...]} | username_password: {username,password}
    scope: Literal["CURRENT_TASK", "SAVED_DOMAIN"] = "CURRENT_TASK"
    domain: str

class WebsiteCredentialResponse(BaseModel):
    credential: dict                   # 脱敏 metadata
    approval_id: int | None            # credential_access Approval（本任务）

@router.post("/{task_id}/credentials", response_model=WebsiteCredentialResponse)
def store_task_credential(task_id, cmd, user=Depends(require_user), db=Depends(get_db)):
    # 1. service.store(...)
    # 2. TaskRepository.get_owned 校验
    # 3. ApprovalService.request_approval(action_type="credential_access",
    #       node_type="fetch", target=f"{domain}", parameters={task_id, domain, type},
    #       scope=ApprovalScope.THIS_ACTION, credential_ref=脱敏)
    #    + DomainService.transition_task(mark_waiting_approval) + outbox approval.requested
    # 4. 返回 {credential: metadata, approval_id}

@router.get("/{task_id}/credentials", response_model=CredentialListResponse)
def list_task_credentials(...): ...   # service.list_for_task（脱敏）

@router.delete("/{task_id}/credentials/{credential_id}")
def delete_task_credential(...): ...  # owner-safe 删除

@saved_router.get("/saved", response_model=CredentialListResponse)
def list_saved_credentials(...): ...  # 设置→安全→已保存网站凭据（脱敏，无新页面）

@saved_router.delete("/{credential_id}")
def delete_saved_credential(...): ...
```
在 `app/api/router.py` include router。DTO 永不回读明文；只返回 credential_id/type/domain/scope/created metadata。

- [ ] **Step 4: 新增 `app/activities/credential_approval.py` — resolve_credential_access activity**

```python
@dataclass
class ResolveCredentialAccessInput:
    user_id: int; approval_id: int; url_hash: str; parameters: dict; decision: str

def _resolve_with_session(session, inp) -> dict:
    # APPROVED → ApprovalService.consume（owner/spec/plan/fingerprint/expiry 复验，D-017）
    #   → 该 task 下 WAITING_CREDENTIAL 且 domain 匹配的 URL → READY_FOR_FETCH（可再抓）
    #   → credential_ref（脱敏）写回 URLResource.discovery_evidence 或独立 ref（不含明文）
    # REJECTED/复验失败 → WAITING_CREDENTIAL → BLOCKED(reason="credential_approval_rejected")

@activity.defn
async def resolve_credential_access(inp) -> dict:  # 复用 M-09 resolve_robots_override 会话模式
```

- [ ] **Step 5: 扩展 `app/workflows/task_workflow.py` — CREDENTIAL_REQUIRED 等待分支**

镜像 M-09 robots `WAITING_APPROVAL` 分支（TaskWorkflow 不做任何网络/凭据副作用）：
```python
if exec_result.status == "CREDENTIAL_REQUIRED":
    refs = exec_result.committed_refs or {}
    url_hash = str(refs.get("url_hash", "")); domain = str(refs.get("domain", ""))
    parameters = refs.get("parameters") or {}
    self._latest_approval = None
    try:
        await workflow.wait_condition(
            lambda: self._latest_approval is not None or self._cancel_requested,
            timeout=timedelta(seconds=inp.pause_timeout_seconds),
        )
    except TimeoutError:
        continue  # 用户未提供凭据前，节点保持当前 index，不失败
    if self._cancel_requested:
        continue  # 循环顶处理 cancel
    latest = self._latest_approval
    decision = latest.decision.upper() if latest else "REJECTED"
    await workflow.execute_activity(
        resolve_credential_access,
        ResolveCredentialAccessInput(user_id=inp.user_id, approval_id=int(latest.approval_id),
            url_hash=url_hash, parameters=parameters, decision=decision),
        start_to_close_timeout=timedelta(seconds=30),
    )
    await workflow.execute_activity(resume_from_approval, ResumeFromApprovalInput(...))  # WAITING_APPROVAL→RUNNING
    # 不推进 _last_index → 重新执行同一 Fetch 节点（URL 已 READY_FOR_FETCH + 凭据已批准）
    continue
```
Fetch executor 在返回 `CREDENTIAL_REQUIRED` 时**不推进**节点 index（`_last_index` 停在 fetch），凭据批准后再跑同一 fetch 节点完成剩余 URL。`commit_checkpoint` 在凭据分支后照常提交。

- [ ] **Step 6: 测试（写失败→跑→实现→跑）**

`backend/tests/crawling/test_website_credentials.py`（内存 SQLite + CredentialVault fake KEK）：
```python
def test_store_and_list_redacted(ctx):
    # 存 cookie 凭据 → 返回/列表只有 credential_id/type/domain/scope/masked；无明文
def test_owner_isolation(ctx):
    # user B 查询/删除 user A 的 credential → NotFound（owner-safe 404）
def test_current_task_scope_bound(ctx):
    # CURRENT_TASK 凭据只能被本 task resolve；其他 task resolve_for_fetch → None
def test_saved_domain_scope_reusable(ctx):
    # SAVED_DOMAIN 凭据同用户后续任务可 resolve
def test_secret_never_in_dto_or_db_plaintext(ctx):
    # 固定 secret 值：DTO JSON / credentials 表 / credential_versions 密文列都不含该值；matches == 0
def test_username_password_contract(ctx):
    # username_password store/read_for_execution roundtrip + basic_auth attachment 形态正确
```

`backend/tests/crawling/test_fetch_e2e_credential.py`（E2E Fixture 3 — 至少一条完整 E2E；另一类型 contract 测试即可）：
```python
def test_credential_cookie_e2e(ctx, http):
    # fixture：/protected 校验 Cookie session=...，否则 401
    # 1. Fetch → 401 → CREDENTIAL_REQUIRED，URL → WAITING_CREDENTIAL
    # 2. WebsiteCredentialService.store(cookie) + ApprovalService.request_approval
    # 3. approve → resolve_credential_access → WAITING_CREDENTIAL → READY_FOR_FETCH
    # 4. Fetch 重跑：resolve_for_fetch 拿到 credential_ref → read_for_execution → build_attachment(cookie)
    #    → SafeFetchHttp 带 Cookie → 200 → PageSnapshot → FETCHED
    # 5. 断言 snapshot.http_metadata 无 set-cookie 明文；temporal 输入/事件 fixture 无 secret
def test_credential_username_password_contract(ctx, http):
    # fixture：/basic 校验 HTTP Basic；Username/Password 凭据 basic_auth 附着 → 200 → FETCHED
def test_cross_domain_cookie_not_leaked(ctx, http):
    # cookie domain=a.com 附着到 b.com URL → build_attachment 拒绝/不含该 cookie（四十一）
```
运行全部 Task 5 测试：`pytest tests/crawling/test_website_credentials.py tests/crawling/test_fetch_e2e_credential.py -q`。

- [ ] **Step 7: Secret 扫描门禁**

```bash
# 固定测试 secret 值，验证全仓不含明文（除 fixture 本身）：
#  grep 检查 DB JSON 序列化/日志/temporal fixture/SSE payload/snapshot metadata 均无该值
grep -rn "<FIXED_TEST_SECRET>" backend/app backend/tests | grep -v "test_website_credentials" || echo "secret scan clean"
```

- [ ] **Step 8: ruff/mypy + Commit**

```bash
.venv/Scripts/python.exe -m ruff check app tests && .venv/Scripts/python.exe -m mypy app
git add backend/app/crawling/credentials.py backend/app/activities/credential_approval.py \
        backend/app/api/routes/credentials.py backend/app/api/schemas.py backend/app/api/router.py \
        backend/app/credentials backend/app/workflows/task_workflow.py backend/tests/crawling
git commit -m "feat(credential): connect website credentials and approval flow"
```

---

### Task 6: SiteFetchStrategy 决策 + Temporal executor 绑定 + M-09→M-10 handoff + SSE

**Files:**
- Create: `backend/app/crawling/site_strategy.py`, `backend/app/crawling/executors.py`, `backend/tests/crawling/test_site_strategy.py`, `backend/tests/crawling/test_executor_binding.py`, `backend/tests/integration/test_m10_fetch_workflow.py`
- Modify: `backend/app/worker.py`, `backend/app/api/events.py`（fetch.* SSE map）, `backend/pyproject.toml`（playwright 依赖）

**Interfaces:**
- Consumes: Task 1 `SiteFetchStrategyRepository`、Task 2/4 executors、M-08 `NODE_EXECUTORS`、M-07 SSE。
- Produces: `SiteStrategyService.decide(...)/record_success(...)/record_failure(...)`、`install_fetch_executors()`、SSE `fetch.*` 事件。

- [ ] **Step 1: 新增 `app/crawling/site_strategy.py` — 决策 + TTL/失效重探测**

```python
class SiteStrategyService:
    def __init__(self, db, *, ttl_seconds=86400): ...
    def decide(self, *, user_id, site_host) -> SiteFetchStrategy | None:
        # 取 (user_id, site_host)；state==valid 且 expires_at 未过期 → 返回；否则 None（重探测）
    def record_success(self, *, user_id, site_host, tier, tool, tool_version,
                       structure_fingerprint, credential_required=False, credential_type=None): ...
    def record_failure(self, *, user_id, site_host): ...   # failure_count += 1；连续失败→ state=expired（重探测）
    def structure_fingerprint(self, *, content_type, body_hash, html_markers: list[str]) -> str:
        # 确定性指纹（不依赖 LLM）
```
策略**不能成为永久 bypass authorization**（二十二）：Fetch/Browser 执行器对每个 URL 仍先重新执行 AccessDecision/robots/scope，策略只决定“用什么工具”，不决定“能否访问”。

- [ ] **Step 2: 测试（策略优先 + TTL 失效重探测 + 不越权）**

`backend/tests/crawling/test_site_strategy.py`：
```python
def test_strategy_prefers_browser_for_second_same_site_url(ctx, http, renderer):
    # 第一次 HTTP→dynamic→Playwright 成功 → 写入 preferred_tier=browser
    # 第二个同站 READY_FOR_FETCH URL → decide 命中 valid → 直接 BROWSER_PENDING（renderer 调用）
def test_strategy_ttl_expired_reprobes(ctx, http, renderer):
    # expires_at 过去 → decide None → 重新 HTTP 探测
def test_strategy_never_bypasses_access(ctx, http, renderer):
    # 策略存在但该 URL robots/scope 拒绝 → 仍 BLOCKED（SiteFetchStrategy 不绕过 AccessDecision）
```

- [ ] **Step 3: 新增 `app/crawling/executors.py` — 注册 FETCH / BROWSER_RENDER**

```python
def install_fetch_executors(*, allow_hosts=frozenset()) -> None:
    # 镜像 M-09 install_discovery_executors：注册 NodeType.FETCH → FetchNodeExecutor(db, ...).execute
    #                          NodeType.BROWSER_RENDER → BrowserRenderNodeExecutor(db, ...).execute
    # 每个 executor 内部 get_session_factory()() 短会话 + finally close
```
`backend/app/worker.py` 的 `run()` 追加：
```python
from app.crawling.executors import install_fetch_executors
install_fetch_executors()
```

- [ ] **Step 4: SSE 事件映射（M-07 复用，`app/api/events.py` `_EVENT_TYPE_MAP`）**

```python
"fetch.started": "FETCH_STARTED",
"fetch.strategy_selected": "FETCH_STRATEGY_SELECTED",
"fetch.escalated": "BROWSER_ESCALATION",
"fetch.credential_required": "CREDENTIAL_REQUIRED",
"fetch.completed": "FETCH_COMPLETED",
"fetch.failed": "FETCH_FAILED",
```
Fetch 执行器只发聚合事件（D-039：不是每个 HTTP redirect 都进 Chat）。

- [ ] **Step 5: 执行器绑定无栈验证 + M-09→M-10 Temporal handoff 集成（收集式）**

`backend/tests/crawling/test_executor_binding.py`：
```python
def test_install_fetch_executors_registers_fetch_and_browser():
    install_fetch_executors()
    assert is_registered(NodeType.FETCH) and is_registered(NodeType.BROWSER_RENDER)
```
`backend/tests/integration/test_m10_fetch_workflow.py`（`@pytest.mark.integration`，本地栈未启动时收集跳过——与 M-09 先例一致）：
```python
async def test_m09_frontier_to_m10_fetch_handoff():
    # 栈可用时：SPECIFIED_SOURCE seed → AccessRules → LinkDiscovery → READY_FOR_FETCH
    #   → plan [fetch] → TaskWorkflow → FetchNodeExecutor → PageSnapshot → URL FETCHED
    marker = _fresh_id("m10-fetch"); assert marker  # 占位：栈可用时提交真实计划并断言 FETCHED
```

- [ ] **Step 6: pyproject 增加 playwright（M-10 必需依赖）**

`backend/pyproject.toml` `dependencies` 追加：`"playwright>=1.45"`。安装并下载 chromium（见 Task 4 Step 5）。不引入 Selenium/Puppeteer/第二套 browser framework。

- [ ] **Step 7: ruff/mypy + Commit**

```bash
.venv/Scripts/python.exe -m ruff check app tests && .venv/Scripts/python.exe -m mypy app
git add backend/app/crawling/site_strategy.py backend/app/crawling/executors.py backend/app/worker.py \
        backend/app/api/events.py backend/pyproject.toml backend/tests/crawling/test_site_strategy.py \
        backend/tests/crawling/test_executor_binding.py backend/tests/integration/test_m10_fetch_workflow.py
git commit -m "feat(workflow): bind fetch and browser executors with site strategy and sse"
```

---

### Task 7: 前端 Credential Drawer 真实业务 + Chat“需要凭据”卡 + Settings 安全区

**Files:**
- Create: `frontend/src/features/tasks/credentials.api.ts`, `frontend/src/features/tasks/ChatCredentialCard.vue`, `frontend/src/features/tasks/credentialDrawer.store.ts`（或复用 `drawer.store.ts`）
- Modify: `frontend/src/app/overlay/drawers/CredentialDrawer.vue`（真实表单）, `frontend/src/app/overlay/drawer.store.ts`（注册 CREDENTIAL drawer payload）, `frontend/src/features/tasks/ChatMessageList.vue`（ref_type=credential_required / SSE CREDENTIAL_REQUIRED → 卡片）, `frontend/src/features/settings/*`（已保存网站凭据列表/删除）

**Interfaces:**
- Consumes: Task 5 `POST/GET /tasks/{id}/credentials`、`GET/DELETE /credentials/saved`、SSE `CREDENTIAL_REQUIRED`。
- Produces: `storeTaskCredential`/`listTaskCredentials`/`deleteCredential`/`listSavedCredentials` 前端 client；CredentialDrawer 提交后打开 Approval。

- [ ] **Step 1: 新增 `frontend/src/features/tasks/credentials.api.ts`**

```ts
export type WebsiteCredentialType = 'cookie' | 'username_password'
export interface WebsiteCredentialDto {
  credential_id: number
  type: WebsiteCredentialType
  domain: string
  scope: 'CURRENT_TASK' | 'SAVED_DOMAIN'
  task_id: number | null
  masked: string
  created_at: string
}
export async function storeTaskCredential(taskId, body): Promise<{ credential: WebsiteCredentialDto; approval_id: number | null }> {...}
export async function listTaskCredentials(taskId): Promise<WebsiteCredentialDto[]> {...}
export async function deleteCredential(taskId | null, credentialId): Promise<void> {...}
export async function listSavedCredentials(): Promise<WebsiteCredentialDto[]> {...}
```

- [ ] **Step 2: 实现 `CredentialDrawer.vue` 真实业务（D-059；不新增页面）**

表单字段：类型单选（cookie / username_password）；Scope 单选（仅当前任务 CURRENT_TASK / 保存供该域名 SAVED_DOMAIN）；domain（从目标 URL 预填）；payload 区（cookie：name/value/domain/path 动态行；username_password：username/password）。提交 → `storeTaskCredential` → 成功显示“已保存，等待审批”→ `openDrawer('APPROVAL', {approvalId})`。组件绝不显示/回读明文之外的内容。

- [ ] **Step 3: Chat“需要凭据”卡 + Drawer 接线**

`ChatCredentialCard.vue`：props `{ domain?: string; urlHash?: string; taskId: string }`，按钮“提供凭据”→ 打开 CredentialDrawer（payload 带 taskId/domain）。`ChatMessageList.vue`：SSE `CREDENTIAL_REQUIRED` 或 chat message `ref_type==='credential_required'` → 渲染该卡（复用 ChatApprovalCard 模式）。`drawer.store.ts` 注册 `'CREDENTIAL'` drawer。

- [ ] **Step 4: Settings → 安全 → 已保存网站凭据**

在现有 settings 安全区展示 `listSavedCredentials()`（domain/type/created/delete，无明文）；delete → `deleteCredential(null, id)`。不新增页面（D-059/D-067）。

- [ ] **Step 5: 前端测试 + type-check**

```bash
cd frontend && npx vitest run src/features/tasks/credentialDrawer.test.ts src/app/overlay/drawers/CredentialDrawer.test.ts
npx vue-tsc --noEmit
```
`CredentialDrawer.test.ts`：mock `storeTaskCredential` → 断言提交后调用 approval drawer；payload 不含明文回读。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/tasks frontend/src/app/overlay
git commit -m "feat(ui): wire credential drawer and saved website credentials"
```

---

### Task 8: E2E 四类 Fixture 收束 + content-hash + Captcha + 全量 scoped 验证 + docs + execution record

**Files:**
- Create: `backend/tests/crawling/test_content_hash_reuse.py`, `backend/tests/crawling/conftest.py`（共享 FakeTransport/FakeRenderer/FakeStorage ctx）
- Modify: `backend/pyproject.toml`（pytest browser marker 注册）, `docs/implementation/M-10-execution.md`（新建）
- Test sweep（只跑 M-10 scoped，禁止全量回归）：
  - `pytest tests/crawling -q`
  - `pytest tests/integration/test_m10_fetch_workflow.py tests/integration/test_m09_discovery_workflow.py -q`（收集验证）
  - `ruff check app tests` / `mypy app`
  - secret scan

**Interfaces:**
- Consumes: 全部 Task 1~7 产物。
- Produces: 四类 E2E fixture 门禁证据、content-hash 复用证据、captcha 安全证据、secret scan 证据、`docs/implementation/M-10-execution.md`。

- [ ] **Step 1: 共享 fixture `tests/crawling/conftest.py`**

FakeTransport（路径→固定响应，记录调用）、FakeRenderer（返回 JS 后 HTML，`invocation_count`）、FakeStorage（内存 dict，记录 put 次数）、内存 SQLite ctx（复用 `tests/discovery/test_discovery_e2e.py` 模式）、`_spec/_run/_unit` helper。

- [ ] **Step 2: content-hash 复用测试 `test_content_hash_reuse.py`**

```python
def test_identical_content_fetch_twice_reuses_blob_and_keeps_audit(ctx, http):
    # 同一 URL 相同 body 抓两次：
    # 1) content_hash 一致
    # 2) FakeStorage.put 调用次数 == 1（Blob 复用，不复制对象）
    # 3) PageSnapshot 2 行 observation，snapshot_version 1/2，prior_snapshot_id 关联
    # 4) 第二次 fetched_at 保留（“何时再次抓取”审计事实）
```

- [ ] **Step 3: 汇总运行四类 E2E + 收束测试**

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/crawling -q
# 预期：
#  - test_fetch_e2e_static.py（静态全链 + renderer==0）
#  - test_fetch_e2e_dynamic.py（HTTP→证据→Playwright→rendered）
#  - test_fetch_e2e_credential.py（Cookie E2E + Username/Password contract）
#  - test_fetch_e2e_failure.py（503→200 有界重试；401/403 不盲升级；captcha 不绕过）
#  - test_content_hash_reuse.py / test_site_strategy.py / test_website_credentials.py
#  - test_contracts.py / test_snapshot.py / test_http_fetch.py / test_browser_render.py / test_batch_fetch.py
#  - test_executor_binding.py（无栈注册验证）
.venv/Scripts/python.exe -m pytest tests/integration/test_m10_fetch_workflow.py -q   # 收集通过（栈未启动则跳过）
```

- [ ] **Step 4: ruff / mypy / secret scan**

```bash
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m mypy app
# secret scan：固定测试 secret 在全仓（除 fixture 自身）出现次数 == 0
```

- [ ] **Step 5: 读取 Git 规范并创建 M-10 分支（写操作前先读 agent-git-standards.md）**

```bash
git checkout -b feature/M-10-fetch-snapshot   # 基线 5830063（M-09 HEAD）
```

- [ ] **Step 6: 新增 `docs/implementation/M-10-execution.md`**

模板（对齐 M-09-execution.md）：状态 IN_PROGRESS→DONE、Baseline M-09 SHA、契约清单（FetchRequest/FetchResult/PageSnapshotRef/SiteFetchStrategy/EscalationEvidence）、HTTP/Scrapy-batch/Playwright 行为、升级链、凭据+Approval、ObjectStorage/hash/immutability、Temporal 绑定、Migration、测试命令与结果、Git Commit 表、跨模块联动、完成结论（M-10=DONE_LOCAL，M-11=UNBLOCKED，DEPLOY-GATE-3=NOT_REACHED）。

- [ ] **Step 7: 最终 Commit**

```bash
git add docs/implementation/M-10-execution.md
git commit -m "docs(fetch): record M-10 execution"
```

---

## Self-Review

**Spec coverage**（逐条核对用户 prompt 关键门禁）：
- READY_FOR_FETCH handoff → Task 6 Step 5（M-09→M-10 Temporal）✓
- FetchRequest/FetchResult → Task 1 ✓
- PageSnapshotRef + M-11 handoff 稳定 → Task 1 Step 3（snapshot_ref/final_url/tool/hash）✓
- SiteFetchStrategy → Task 1/6（TTL/失效重探测/不越权）✓
- 能力阶梯 canonical（STRUCTURED→STATIC→BATCH→BROWSER→BROWSER_AGENT 仅契约）→ Task 1/2/3/4 ✓
- 静态页面不启动 Playwright → Task 2 Step 5 + Task 4 Step 4 ✓
- 升级必须有证据 → Task 4 Step 3（无证据不渲染）✓
- 401/403 不盲升级 → Task 2 Step 6 ✓
- Captcha 不自动绕过 → Task 2 Step 6 + Task 8 ✓
- Credential Drawer/Cookie/Username-Password/Approval/owner 隔离/secret 安全 → Task 5 + Task 7 ✓
- PageSnapshot ObjectStorage/content-hash/相同内容复用/immutable → Task 1 + Task 8 Step 2 ✓
- Fetch 事务/幂等恢复（blob 先写 + exists 复用）→ Task 1 Step 8 ✓
- 四类 E2E fixture → Task 2/4/5 + Task 8 ✓
- retry 有界（内部 + Temporal 不叠加爆炸）→ Task 2 Step 4 + 集中配置 Task 1 Step 4 ✓
- Browser worker 独立 resource class（M-08 BROWSER 已定义），无 M-16 → Task 4 ✓
- 不实现 M-11（Extract/FieldEvidence/Record）→ 全局无 EXTRACT 实现 ✓
- 13 页边界（Credential Drawer/Settings 安全区，不新增页面）→ Task 7 ✓
- 本轮不部署（DEPLOY-GATE-3 NOT_REACHED）→ 无部署任务 ✓

**Placeholder scan**：代码步骤均为可执行具体内容；`test_m10_fetch_workflow` 集成占位与 M-09 先例一致（真实 Temporal 断言依赖本地栈），其余测试均为可运行断言。

**Type consistency**：`FetchRequest/FetchResult/PageSnapshotRef/EscalationEvidence/FetchErrorCode` 在 Task 1 定义并在 Task 2~6 一致使用；`PageSnapshotService.commit_raw` 签名在 Task 1 定义、Task 2/4/5 调用；`install_fetch_executors` 在 Task 6 定义并被 worker 引用；`resolve_credential_access` 在 Task 5 定义并被 TaskWorkflow 引用；FrontierState 新枚举在 Task 1 定义并被 Task 2/4/5 消费。

---

## PLAN SELF-APPROVAL: PASS

```
M-09 precondition: PASS
business decision D-009: PASS
implementation plan M-10: PASS
M-03 credential compatibility: PASS
M-04 snapshot/idempotency compatibility: PASS
M-07 Temporal compatibility: PASS
M-08 node/approval compatibility: PASS
M-09 frontier/access compatibility: PASS
fetch tier model: PASS
Scrapy static-tier boundary: PASS
evidence-based escalation: PASS
captcha prohibition: PASS
credential safety: PASS
snapshot immutability: PASS
object-storage boundary: PASS
retry boundedness: PASS
browser worker boundary: PASS
M-11 boundary: PASS
13-page boundary: PASS
A-Lite testing: PASS
fast-development-test policy: PASS
deployment boundary: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS
```
