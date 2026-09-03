# Kairos Phase 3B Closure + Phase 4 Browser Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 封板 Phase 3B（真实 EXPLORATORY 采集已全 PASS），然后实现 Phase 4 Browser Escalation：HTTP 永远优先，仅当 Agent 判断 HTTP Snapshot 不足时，通过 durable `kairos-agent-v1` 调用稳定 `kairos-browser-v1` 的 `request_browser_task` 工具，在独立 Temporal Activity 内启动临时 NON-DURABLE Playwright Browser Agent（Harness `PlaywrightBrowser` + Chromium），产出 Browser-rendered `PageSnapshot`（MinIO raw/text/screenshot），回到主 Agent 继续 `inspect_snapshot` → `commit_extraction` → deterministic Evidence/Completion。

**Architecture:** 单一主 Agent `kairos-agent-v1` 不变（Pydantic AI + `TemporalDurability`）。新建 `BrowserTask` 领域对象（唯一键 `(task_run_id, source_id)`）、`CollectionSource.BROWSER_REQUIRED` 状态、`PageSnapshot` capture_method/parent/screenshot 扩展。`request_browser_task` 由 `kairos-browser-v1` FunctionToolset 提供，经 `TemporalDurability` 成为隔离的 Browser Activity；`BrowserTaskRunner` 在该 Activity 内创建无 TemporalDurability 的临时 Pydantic AI Agent（model 复用 ModelResolver/DeepSeek config、capabilities=[Harness `PlaywrightBrowser`]，`allowed_domains` 来自 source hostname + spec scope_domains，`block_private_addresses=True`，`FilteredToolset` 只暴露只读工具）。Chromium 生命周期完全封闭在 Activity 内。Browser 成功 → Browser PageSnapshot(`capture_method=BROWSER`, parent_snapshot_id=HTTP snapshot)、source 回到 FETCHED 并指向 Browser snapshot → 主 Agent inspect/commit → FieldEvidence 从 Browser clean text 验证。禁止第二套 Agent/PageSnapshot/Completion。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy async, Alembic, PostgreSQL, Temporal 1.32 / `temporalio==1.32.0`, Pydantic AI `TemporalDurability`, `pydantic-ai-harness[playwright]`（editable，`PlaywrightBrowser`/`EgressPolicy`/`PlaywrightBrowserSession`/`FilteredToolset`）, Playwright Chromium（`playwright install chromium`）, HTTPX, MinIO, Vue 3 + TypeScript strict, pytest/ruff/vue-tsc。

**Spec:** 本 plan 即已批准设计规范（用户提示词 PART 1 §20～§135）。实现边界由 §21（Harness PlaywrightBrowser 真实 API）、§22（Harness+Temporal 不可组合的源码结论）、§43（真实 tool names）、§55（PromptInjectionDefender）、§96（quotes.toscrape.com JS fixture）、§97（SPECIFIED_SOURCE smoke）固定。外部开源（browser-use/stagehand/skyvern）仅作架构参考，禁止引入它们作为 runtime。

## PART 0 — Phase 3B Closure（已完成，记录封板证据）

**状态：COMPLETE（2026-09-03）**

- 基础设施根因：`kairos-local-temporal-1/postgres/minio/ui` 在 2026-09-02T12:27:28–29Z 同时 `ExitCode=0`、`OOMKilled=false`、`RestartCount=0` → 整体 Docker Desktop/WSL2 引擎被干净关闭，非容器内故障，非 OOM，非 healthcheck/restart。已恢复并全程监控（smoke 期间 restart=0 / oom=false）。
- 确定性测试：核心 Phase 3B 测试全绿（`test_collection_dedup`, `test_collection_completion_phase3b`, `test_search_sources`, `test_search_provider`, `test_phase3b_domain`, `test_exploratory_workflow`, `test_collection_extraction`, `test_collection_completion`, `test_phase3b_api`, `test_collection_repositories`）合计 42 passed。
- `alembic upgrade head` → `0003_phase3b_discovery (head)`；`ruff check backend/app backend/tests` → All checks passed；`vue-tsc --build` → PASS。
- Provider preflight：`search_provider_preflight.py` → `provider=tavily results=3 PASS`；DeepSeek `Reply exactly: OK` → `OK`。
- 真实 EXPLORATORY smoke（`tests/smoke/exploratory_collection_smoke.py`, target_count=5）：**exit 0**，终态 JSON 持久化于 `docs/audits/phase3b-exploratory-smoke-2026-09-03.json`。
- 六个 Gate 全 PASS → **PHASE 3B COMPLETE** → 已切到 `feature/phase-4-browser-escalation` 分支。

不再重复开发 Phase 3B。Phase 4 在干净分支上增量实现，不重写既有 Search/Discovery/Dedup/Tavily/Evidence/Agent Runtime/Workspace。

---

## Global Constraints

- 唯一主业务 Agent 永远是 `kairos-agent-v1`（Pydantic AI + `TemporalDurability` + `kairos-web-v1`/`kairos-collection-v1`/`kairos-search-v1`/workspace toolsets）。禁止创建第二个业务 Agent（BrowserAgent/KairosCrawlerAgent 等）。Phase 4 只允许一个临时 NON-DURABLE Browser Task Agent，且仅是 `BrowserTaskRunner` 内部实现细节：不掌握 Task lifecycle、不 Search、不写 Record、不做 Dedup、不判断 Completion、不拥有 CollectionSpec、不成为 Temporal Workflow Agent，Activity 结束立即销毁。
- 禁止 `Agent(capabilities=[TemporalDurability(...), PlaywrightBrowser(...)])` — Harness `for_agent` 检测到 `BaseDurabilityCapability` 即 `UserError`。架构：`kairos-agent-v1 → request_browser_task → Temporal Activity → BrowserTaskRunner → 临时 Agent(PlaywrightBrowser) → wakeless session → PageSnapshot → 返回 → inspect_snapshot → commit_extraction`。
- HTTP 永远优先；Browser 是昂贵 escalation path（§20）。`browser_hint` 只是提示，最终是否 escalation 由主 Agent 判断（§113）；404/410/robots/auth/captcha/unsupported content 不得成为 LIKELY_JS_REQUIRED（§114）。
- 不默认把任意自然语言 goal 交给主模型；`request_browser_task(source_id)` 只接受 `source_id`（§36），可接受 `reason` enum（JS_RENDER_REQUIRED/CONTENT_HIDDEN/EXPAND_REQUIRED）最大（§38）。URL 由服务端从 `CollectionSource.url` 解析，模型不传 URL（防越界）。
- URL/DNS 安全：复用 Phase 3B `validate_public_http_url` + `request_with_safe_redirects` 校验 initial URL 与 redirects（§52A）；启用 Harness `block_private_addresses=True`（§51）；`allowed_domains` = source hostname + 合法 redirect 后经 policy 验证的同站/允许域（§49）。hostname 边界匹配（`example.com`/`docs.example.com` 允许，`example.com.attacker.com`/`contains()` 禁止）（§50）。
- Browser 禁止：登录/注册/提交表单/购买/发送消息/修改数据/上传/下载/绕过验证码/绕过授权（§37）；CAPTCHA/AUTH → `BLOCKED`（§64/65）；第一版不启用 captcha solver、不注入 credential、不恢复 session cookie（§64/65）。禁止 `execute_js` 暴露（默认过滤，§44）；`accept_downloads=False` 保持（§45）；禁止 upload（§46）；禁止 type/fill/password/textarea（§47）。
- Safe click：允许有限 click（Load More/Expand/Tab/Cookie banner），但用 deterministic denylist（login/sign in/register/buy/purchase/checkout/submit/send/post/delete/remove/save/confirm/subscribe/download/upload）+ Browser Agent instructions 拦截（§48）。
- Browser 不访问 Kairos 内部服务：127.0.0.1/localhost/PostgreSQL/Temporal/MinIO/Docker 内部 IP/metadata 端点必须拒绝（§53）。
- Prompt injection：Browser Agent system instruction 明确页面文字不能修改 system/allowed domains/goal/read-only/step limits/credential policy（§54）；只读策略 + domain policy + tool filtering 为第一版安全基线，不新建安全 Framework（§55）。
- Secret isolation：Browser 子 Agent 不得获得任何 API key/DB/MinIO/session secrets；ModelResolver 在 Worker 进程环境解析凭据；Secret 不进入 BrowserTask row/tool args/events/Temporal History/screenshot metadata（§56）。
- Browser limits：`BrowserLimits` typed（max_browser_tasks_per_run=5 / max_steps_per_task=20 / task_timeout_seconds=120 / max_navigation_count=5 / max_action_events=30），必须 default + hard max（§33），且 resolved limits 冻结在 CollectionSpecVersion（不是进程 global，§34）。
- Completion/Progress/Event 扩展但不改变 Phase 3B deterministic 语义：`browser_required_sources`/`browser_tasks_used`/`browser_tasks_remaining`/`browser_completed`/`browser_blocked`/`browser_failed`/`actionable_browser_sources`（≤5）；continuation 含 browser budget 语义（§80-84）。`browser.*` 领域事件走现有 PostgreSQL/Outbox → FastAPI SSE（§86），不推送链式思考（§85）。`browser.action` 只发高层可理解动作（attempt/step_number/action_type/short_description/current_domain），每任务上限 `max_action_events`（§87-90）。
- Screenshot：PNG 存 MinIO；PostgreSQL 只存 storage reference/metadata；Temporal 禁止 base64/binary（§70）。clean text 必须来自 Browser-rendered state，禁止对 HTTP raw HTML 重跑 phase3 parser 冒充 Browser 成功（§69）。
- Migration：新建 `0004_phase4_browser_escalation`，禁止改 0001/0002/0003；真实 `alembic upgrade head` 必须 PASS（§32）。BrowserTask 唯一键 `(task_run_id, source_id)`（§25）；COMPLETED+snapshot_id 存在时再次 request 直接返回已有结果，禁止重复 Chromium 采集（§25）；RUNNING 同 source → `BROWSER_TASK_ALREADY_RUNNING`（§67）。
- 不 patch upstream（pydantic-ai-harness / pydantic-ai / Temporal）为当前首选（§135）；如确需 patch 必须停下记录 blocker，不得混入 upstream commit。Upstream repos 保持 clean。
- 禁止提前实现 Phase 5：Continue-As-New、Pause/Resume、full Cancel semantics、Workflow Versioning、long-run compaction 全部不做（§136）。禁止引入 Browser Use/Stagehand/Skyvern 作为 runtime（§4）。禁止 git push origin（无论 upstream 还是 Kairos 远程）。
- 只做当前范围：不重构 SearchProvider/Tavily/Frontier/Dedup/CollectionSpec/Evidence/Agent Runtime/Workspace，除非真实验收暴露明确 blocker。
- 测试范围：browser policy tests / BrowserTask repo+idempotency / BrowserTaskRunner integration / completion tests / event tests / API screenshot tests / Phase 3 regressions，每次只跑 targeted（§122）。Lint 只针对本轮 changed Python files（ruff）+ 前端 typecheck（§123）。不引入 frontend Playwright E2E。

---

# PART 1 — Phase 4 Browser Escalation

## 文件结构锁定

```text
backend/app/browser/
  __init__.py          # 领域导出
  domain.py            # BrowserLimits / BrowserTaskStatus / BrowserTaskReason / User
  models.py            # BrowserTask SQLAlchemy 模型（BrowserTask）
  repository.py        # owner-scoped BrowserTask 事务（claim/complete/fail/idempotent）
  policy.py            # 可复用 policy：allow/reject（run budget、renderable、read-only、domain、private、
                       #  时只读判据）；accessibility/text 为优先，不依赖 vision
  runner.py            # BrowserTaskRunner：resolve source→policy→temporary Agent(PlaywrightBrowser)
                       #  →bounded steps→PageSnapshot(minio)→BrowserTask COMPLETED
  events.py            # browser.* 事件 persist（复用 bounded EventEnvelope）
backend/app/agent/browser_tool.py   # request_browser_task FunctionToolset (id=kairos-browser-v1)
backend/app/agent/runtime.py        # 修改：注册 kairos-browser-v1 + browser 独立 Activity config
backend/app/domain.py               # 修改：BROWSER_REQUIRED/BLACKLIST 状态、BrowserLimits、
                                    #  Progress/Decision 扩展
backend/app/models.py               # 修改：CollectionSource/PageSnapshot/CollectionSpecVersion Browser 字段
backend/app/repositories.py         # 修改：source transition（BROWSER_REQUIRED→FETCHED→PROCESSED）
backend/app/activities.py           # 修改：加载/保存 BrowserTask 的服务（lean）
backend/app/api.py                  # 修改：screenshot 路由 / snapshot metadata / browser events / browser task status
backend/alembic/versions/0004_phase4_browser_escalation.py
backend/tests/test_browser_policy.py
backend/tests/test_browser_task_repository.py
backend/tests/test_browser_runner.py
backend/tests/test_browser_completion.py
backend/tests/test_browser_events.py
backend/tests/test_browser_api.py
backend/tests/test_phase3b_regression.py   # 新建：确保普通 HTTP Source 不触发 Browser
tests/smoke/specified_source_browser_smoke.py  # 权威 P4-F vertical smoke
frontend/src/api.ts            # 扩展 DTO
frontend/src/App.vue           # snapshot 元数据 + screenshot 查看（最小）
```
  边界：领域层不依赖 Playwright；`BrowserTaskRunner` 持有 `PlaywrightBrowserSession`，但 runner 是模块私有实现，不允许出现在 `CollectionSource` transition / Completion 判断里。

## 前期任务（第 0 组 — 依赖与基础设施）

### Task 0: Playwright 依赖 + Chrome 安装 + Harness 组合约束验证

**Files:**
- Modify: `backend/pyproject.toml`（仅加注释说明 `pydantic-ai-harness` 可选 extra 用法，不改变现有依赖列表；实际加的启动器命令见 Step 2）
- Test: `backend/tests/test_browser_harness_boundary.py`（新建）

**Interfaces:**
- Produces: 事实一：`PlaywrightBrowser + TemporalDurability` 组合会 raise `UserError`（由 Harness `for_agent` 检测 `BaseDurabilityCapability`）。事实二：当前 Harness 未随 editable 安装 playwright，需装 `[playwright]` extra + Chromium。

- [ ] **Step 1: 检查 `playwright` 是否已随环境可用**

Run: `cd backend && uv run python -c "import playwright; print(playwright.__version__)"`.
Expected: `ModuleNotFoundError`（当前未安装）或打印版本。

- [ ] **Step 2: 安装 Harness Playwright extra（不改 upstream）**

Run（在 `backend/`）：
```bash
uv pip install --editable "/d/Develop/kairos/pydantic-ai-harness[playwright]"
```
Expected: `playwright` 可导入。

- [ ] **Step 3: 安装 Chromium（只装 chromium，不装 firefox/webkit）**

Run（在 `backend/`）：
```bash
uv run playwright install chromium
```
Expected: `Chromium downloaded to ...` 且无其他浏览器下载。若本步因网络/缓存失败，记录为 infra blocker 暂停，不绕过。

- [ ] **Step 4: 写失败测试：断言组合被拒**

Create `backend/tests/test_browser_harness_boundary.py`:
```python
import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.exceptions import UserError
from pydantic_ai.durable_exec.temporal import TemporalDurability
from pydantic_ai_harness.playwright import PlaywrightBrowser

@pytest.mark.asyncio
async def test_durable_plus_browser_is_rejected():
    with pytest.raises(UserError, match="does not support durable execution"):
        Agent(
            "kairos:local-model",
            capabilities=[TemporalDurability(), PlaywrightBrowser()],
            defer_model_check=True,
        )
```
Expected: 构造期即 `UserError`（证明 Harness 的 `for_agent` guard 生效）。

- [ ] **Step 5: 写局部 Chromium 生命周期 integration（不依赖 DeepSeek）**

```python
import asyncio
from pydantic_ai_harness.playwright import EgressPolicy, PlaywrightBrowserSession

async def main():
    async with PlaywrightBrowserSession(policy=EgressPolicy()) as session:
        page = await session.ensure_page()
        await page.goto("https://quotes.toscrape.com/js/")
        await page.wait_for_load_state("domcontentloaded")
        title = await page.title()
        text = await page.inner_text("body")
        print("TITLE:", title, "| HAS_QUOTES:", "“" in text or "quote" in text.lower())
    print("SESSION CLOSED")

asyncio.run(main())
```
Run: `uv run python - <<'PY' ... PY`
Expected: 打印非空 title、页面含 quote 内容、`SESSION CLOSED` 且进程退出（无孤立 Chromium）。这是 P4-C 的先导验证。

- [ ] **Step 6: 跑该测试文件**

Run: `uv run --project backend pytest backend/tests/test_browser_harness_boundary.py -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/tests/test_browser_harness_boundary.py
git commit -m "feat(browser): install harness playwright extra and verify durability boundary"
```

### Task 1: Browser 领域模型 + Migration 0004

**Files:**
- Modify: `backend/app/domain.py`
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0004_phase4_browser_escalation.py`
- Create: `backend/tests/test_browser_domain.py`（domain 模型测试）
- Create: `backend/tests/test_browser_migration.py`（migration 真实 upgrade 测试）

**Interfaces:**
- Consumes: `CollectionSourceStatus`（即加 `BROWSER_REQUIRED`）、`CollectionSpecVersion`、`PageSnapshot`。
- Produces:
  - `enum BrowserTaskStatus { PENDING, RUNNING, COMPLETED, FAILED, BLOCKED }`
  - `enum BrowserFailureCode { AUTH_REQUIRED, CAPTCHA_REQUIRED, ACCESS_DENIED, ROBOTS_BLOCKED, OUT_OF_SCOPE, UNSUPPORTED_INTERACTION, READ_ONLY_POLICY_BLOCKED, BROWSER_UNAVAILABLE, TIMEOUT, TRANSIENT_RETRY, INTERNAL }`
  - `enum BrowserTaskReason { JS_RENDER_REQUIRED, CONTENT_HIDDEN, EXPAND_REQUIRED }`
  - `class BrowserLimits(model_config=extra forbid): max_browser_tasks_per_run:int=5 (1..10); max_steps_per_task=20 (1..50); task_timeout_seconds=120 (30..300); max_navigation_count=5 (1..20); max_action_events=30 (5..100)`
  - `class BrowserTask(BaseModel): browser_task_id, owner_id, task_id, task_run_id, spec_version_id, source_id, status, attempt_count, snapshot_id|None, policy_version, failure_code|None, failure_message|None, started_at|None, completed_at|None, created_at, updated_at`
  - `class BrowserTaskSummary(BaseModel): browser_task_id, source_id, source_url, status, attempt_count, snapshot_id|None, failure_code|None, failure_message|None, started_at|None, completed_at|None`
  - `class BrowserTaskResult(BaseModel): browser_task_id, source_id, status, snapshot_id|None, capture_method='BROWSER', attempt_count, failure_code|None, failure_summary|None`（request_browser_task 的 bounded 返回，§57）
  - `class RequestBrowserTaskInput(BaseModel): source_id:str; reason: BrowserTaskReason|None=None`
  - Progress 扩展字段（default=0/空以保持兼容）：`browser_required_sources:int=0; browser_tasks_used:int=0; browser_tasks_remaining:int=0; browser_completed:int=0; browser_blocked:int=0; browser_failed:int=0; actionable_browser_sources:list[CollectionSourceSummary]=[]`
  - `evaluate_collection_completion` 增加 `browser_required_sources: int, browser_tasks_remaining: int` 关键字参数；新产 `BROWSER_TASK_LIMIT` reason（decision=PARTIALLY_COMPLETED 且 reason 稳定）。

**模型变化（models.py）：**
- `CollectionSpecVersion`（可通过 0004 加列）加 `browser_limits_json: JSON, default=BrowserLimits().model_dump()`；`browser_policy_stack_version: str, default="browser-policy-v1"`。
- `CollectionSource` 加 `browser_required_reason: String(32)|None`、`browser_task_id(64)|None`。
- `PageSnapshot` 加 `capture_method: String(16), default='HTTP'`、`parent_snapshot_id: String(64)|None`、`screenshot_storage_key: Text|None`、`rendered_at: DateTime|None`（raw/text 继续存 MinIO）。
- 新表 `browser_tasks`（见 Task 8 结构，但在这里先建模型）：
  - `browser_task_id: String(64) PK`
  - `owner_id/task_id/task_run_id/spec_version_id/source_id`（含 FK/索引）
  - `status: String(32) index`、`attempt_count:Integer default 0`、`snapshot_id: String(64)|None`、`policy_version: String(64)`、`failure_code/message|None`、`started_at/completed_at: DateTime|None`、`created_at/updated_at`
  - `UniqueConstraint(task_run_id, source_id)`
- `0004_phase4_browser_escalation.py`：全部 `op.add_column` 到现有表 + `op.create_table("browser_tasks")` + 唯一约束 + 索引；不改 0001/0002/0003。

**决定性代码片段（domain）：**
```python
class BrowserLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_browser_tasks_per_run: int = Field(default=5, ge=1, le=10)
    max_steps_per_task: int = Field(default=20, ge=1, le=50)
    task_timeout_seconds: int = Field(default=120, ge=30, le=300)
    max_navigation_count: int = Field(default=5, ge=1, le=20)
    max_action_events: int = Field(default=30, ge=5, le=100)
```
`bounded_payload` 已过滤 secret 形状字段，BrowserTask payloads 复用。

- [ ] **Step 1: 写失败 domain 测试**

`test_browser_domain.py` 断言：`BrowserLimits()` 默认值正确；`BrowserLimits(max_browser_tasks_per_run=99)` 校验失败；`BrowserTask` 构造默认 status=PENDING、attempt_count=0；`evaluate_collection_completion` 新增关键字参数下 target未达到+browser_required+budget→CONTINUE；target未达到+BROWSER_REQUIRED+remaining=0→PARTIALLY_COMPLETED/`BROWSER_TASK_LIMIT`；browser blocked+search budget→CONTINUE；browser 成功 target 达到→COMPLETED（§111 A–D）。

- [ ] **Step 2: 跑测试到预期失败**

Run: `uv run --project backend pytest backend/tests/test_browser_domain.py -q`
Expected: FAIL（缺 symbol/字段）。

- [ ] **Step 3: 实现 domain 扩展 + migration**

在 `domain.py` 增加枚举/模型/Progress 字段，并在 `evaluate_collection_completion` 的 `elif` 链最末（technical/ target/continuation/saturation/rounds/discovered/processed 之后）追加：
```python
elif browser_required_sources > 0 and browser_tasks_remaining <= 0:
    decision, reason = "PARTIALLY_COMPLETED", "BROWSER_TASK_LIMIT"
```
注意顺序：Browser budget 耗尽仅在 target 未达且搜索/处理预算未触发时介入。在 `models.py` 加列/表，写 `0004_phase4_browser_escalation.py`。

- [ ] **Step 4: 跑 migration 到真实 PG**

Run: `uv run --project backend alembic -c backend/alembic.ini upgrade head`
Expected: revision `0004_phase4_browser_escalation -> head`。再次跑 `alembic current` 确认 head=`0004_phase4_browser_escalation`。

- [ ] **Step 5: 跑 domain + migration 测试**

Run: `uv run --project backend pytest backend/tests/test_browser_domain.py backend/tests/test_browser_migration.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain.py backend/app/models.py backend/alembic/versions/0004_phase4_browser_escalation.py backend/tests/test_browser_domain.py backend/tests/test_browser_migration.py
git commit -m "feat(browser): add browser task domain model and migration"
```

### Task 2: Browser 策略（policy.py）— 确定性 denylist + egress + budget 校验

**Files:**
- Create: `backend/app/browser/policy.py`
- Create: `backend/tests/test_browser_policy.py`

**Interfaces:**
- Consumes: `BrowserLimits`, `CollectionSource`, `validate_public_http_url`, `EgressPolicy`（Harness）。
- Produces:
  - `HIGH_RISK_ACTION_NAMES` frozenset（login/sign in/register/buy/purchase/checkout/submit/send/post/delete/remove/save/confirm/subscribe/download/upload，§48）。
  - `def action_is_read_only(name: str) -> bool`（不在 denylist 且非 type/fill/password）。
  - `def allowed_domains_for(source, scope_domains, spec_browser_domains) -> list[str]`：source hostname + 显式 `scope_domains` 交集 + spec 显式 allow（可空）；返回规范化 host 列表。用 `urlparse(source.url).hostname.lower().rstrip('.')`。
  - `def resolve_browser_domains(source_url: str, scope_domains: list[str], explicit_extra: list[str]) -> list[str]`，空时默认 `[hostname]`。
  - `def egress_policy_for(source_url, scope_domains, explicit_extra=None) -> EgressPolicy`：`block_private_addresses=True`、`allowed_domains=resolve_browser_domains(...)`、`include_subdomains=True`、`allowlist_reach=frozenset({'navigation','data'})`。
  - `def classify_url(url) -> Literal['ok','private','non_http','unknown']`（复用 `validate_public_http_url`，捕获 `CollectionError`）。
  - `def policy_version() -> str`：返回 `"browser-policy-v1"`。
  - hard-cap helper：`def server_browser_limits(limits: BrowserLimits) -> BrowserLimits`（reterade:/clamp each field to its hard max）。

- [ ] **Step 1: 写失败测试**

覆盖：denylist 命中 `submit/buy/sign in/register/login` → 非 read-only；`click 文案 "Load More"/"Expand"` 允许；`allowed_domains_for` 返回 `['quotes.toscrape.com']`；`scope_domains=['toscrape.com']` 时 `click・docs.example.com` 允许（子域）；`example.com.attacker.com` 被过滤；`egress_policy_for` 的 `block_private_addresses is True` 且 `allowed_domains` 不含通配符；`classify_url('http://127.0.0.1/x')=='private'`、`classify_url('file:///etc/passwd')=='non_http'`。

- [ ] **Step 2: 跑测试到失败**

Run: `uv run --project backend pytest backend/tests/test_browser_policy.py -q` → FAIL。

- [ ] **Step 3: 实现 policy.py**

写入上述函数。`allowed_domains_for` 必须做 hostname boundary：`host == domain or host.endswith('.'+domain)`，且禁止通配符/点开头（与 Harness `EgressPolicy.__post_init__` 一致）。用 Harness `_to_idna` 同款规则（直接复用 `idna`）。

- [ ] **Step 4: 跑测试 + ruff**

Run: `uv run --project backend pytest backend/tests/test_browser_policy.py -q && uv run --project backend ruff check backend/app/browser/policy.py backend/tests/test_browser_policy.py`
Expected: PASS + All checks passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/browser/policy.py backend/tests/test_browser_policy.py
git commit -m "feat(browser): add egress budget and read-only browser policy"
```

### Task 3: BrowserTask 仓库（repository.py）— claim / idempotent complete / fail / list

**Files:**
- Create: `backend/app/browser/repository.py`
- Create: `backend/tests/test_browser_task_repository.py`

**Interfaces:**
- Consumes: `BrowserTask`(domain), `TaskRun`, `CollectionSource`, `PageSnapshot` 模型；`session_scope`。
- Produces:
  - `async def get_browser_task_for_source(task_id, task_run_id, owner_id, source_id) -> BrowserTask|None`
  - `async def claim_browser_task(task_id, task_run_id, owner_id, spec_version_id, source_id, policy_version, browser_limits) -> BrowserTask|None`：owner-scoped；检查 source 属于 run 且为 `PENDING/FETCHED/BROWSER_REQUIRED`；若既有 COMPLETED 且 snapshot 存在 → 直接返回该 task（idempotent，不再 claim）；若既有 RUNNING → 返回 None（表示 concurrent duplicate → `BROWSER_TASK_ALREADY_RUNNING`）；若无 → 创建 `PENDING` 并 CAS 为 `RUNNING`（`attempt_count=1`）。事务内 `with_for_update`。
  - `async def record_browser_attempt_prepare(...)`（retry 时 `attempt_count+1`，复用同一 task，§29/§66）。
  - `async def complete_browser_task(browser_task_id, task_id, task_run_id, owner_id, snapshot_id, source_complete=True) -> BrowserTask`：仅 `RUNNING→COMPLETED`；幂等处理重复调用。
  - `async def fail_browser_task(browser_task_id, task_id, task_run_id, owner_id, failure_code, failure_message, terminal=True) -> BrowserTask`：`RUNNING→FAILED`（技术 transient → `FAILED` + `TRANSIENT_RETRY` 供 retry 检测；业务 blocked → `BLOCKED`，terminal=True）。
  - `async def mark_browser_blocked(source_id, task_id, task_run_id, owner_id, failure_code) -> None`：source 到 `BLOCKED`（§63），并 `_complete_search_round_if_terminal`。
  - `async def list_browser_tasks_for_run(task_id, task_run_id, owner_id) -> list[BrowserTaskSummary]`
  - helper：owner-scoped null-safe source 校验用 `CollectionSource` 查询。

- [ ] **Step 1: 写失败测试**

覆盖（§66/§67/§25/§108/§110）：重复 request 同 source 且 COMPLETED → 返回同一 browser_task_id 且不新增行；RUNNING 时再次 claim → None；claim 成功创建 PENDING 并转 RUNNING；`attempt_count` 递增；retry 不产生第二条 task；BLOCKED 后再次 request 同 source → 不自动重试；全部 owner-scoped；同时 `browser_tasks` 唯一键 `(task_run_id, source_id)` 生效。

- [ ] **Step 2: 跑测试到失败**

- [ ] **Step 3: 实现 repository.py**

用 `session_scope`，全部查询带 owner/task/run 过滤，并用 `CollectionSource` 的 `snapshot_id`/`status` 做权威校验。`complete_browser_task` 里把 `source.snapshot_id = snapshot_id; source.status = FETCHED`（§27 的 BROWSER_REQUIRED→FETCHED 路径在 runner 成功时完成）；`mark_browser_blocked` 里 source→`BLOCKED`。

- [ ] **Step 4: 跑测试 + ruff**

- [ ] **Step 5: Commit**

```bash
git add backend/app/browser/repository.py backend/tests/test_browser_task_repository.py
git commit -m "feat(browser): add owner-scoped browser task repository and idempotency"
```

### Task 4: 主 Agent 挂载 `kairos-browser-v1` 工具 + 独立 Activity 配置

**Files:**
- Create: `backend/app/agent/browser_tool.py`
- Modify: `backend/app/agent/runtime.py`
- Modify: `backend/app/agent/deps.py`（如需要加 browser_policy_version 只读字段）
- Create: `backend/tests/test_browser_toolset.py`

**Interfaces:**
- Consumes: `request_browser_task` 的输入/输出（见 Task 5/6 定义的 Activity），`KairosAgentDeps`。
- Produces:
  - `BROWSER_TOOL_ACTIVITY_CONFIG`（走 TemporalDurability toolset_activity_config）：`start_to_close_timeout=timedelta(seconds=240)`（2–3 min，§59）+ `retry_policy`（`maximum_attempts=2`, `initial_interval=timedelta(seconds=5)`, `maximum_interval=timedelta(seconds=20)`, `non_retryable_error_types=[BrowserTaskBlockedError.__name__]`，transient 才 retry）。
  - `browser_toolset = FunctionToolset([request_browser_task_tool], id="kairos-browser-v1", metadata={"temporal": BROWSER_TOOL_ACTIVITY_CONFIG}, instructions=...)`。
  - `kairos_agent` 增加 `toolsets=[..., browser_toolset, ...]`，但 toolsets 固定在构造期（不 runtime 注入，Pydantic AI 现状）。
  - `kairos-browser-v1` 的 instructions：HTTP FIRST；fetch_source → inspect_snapshot → 若正文不足且字段仍缺，才调 `request_browser_task`；禁止对 robots/private/403/captcha/unsupported content 调 browser（§77）；成功后重新 inspect Browser snapshot 再 commit；不 commit 空 records 而跳过（§78）。

- [ ] **Step 1: 写失败测试**

断言 `kairos_browser_v1` 存在于 `kairos_agent` 的 toolsets 且 id 稳定、`request_browser_task` 名字注册、`BROWSER_TOOL_ACTIVITY_CONFIG` 有独立 timeout/retry（不覆盖其他 toolset 的 `COLLECTION_TOOL_ACTIVITY_CONFIG`）。

- [ ] **Step 2: 跑测试到失败**

- [ ] **Step 3: 实现 browser_tool.py + 修改 runtime.py**

在 `agent/browser_tool.py` 定义 `BrowserToolClosedError`（不可重试业务错误 marker）与 `request_browser_task(ctx: RunContext[KairosAgentDeps], input_data: RequestBrowserTaskInput) -> BrowserTaskResult`。Tool 内只做 scope 校验与调用 repository/runner（真正 Chromium 在 Activity）。`runtime.py` 增量：import 新 toolset + activity config，加到 `kairos_agent` 的 toolsets 与 `_durability.toolset_activity_config`。

- [ ] **Step 4: 跑测试 + ruff + 既有 workflow/collection tests（回归）**

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/browser_tool.py backend/app/agent/runtime.py backend/app/agent/deps.py backend/tests/test_browser_toolset.py
git commit -m "feat(agent): add kairos-browser-v1 toolset to durable main agent"
```

## 中期任务（第 1 组 — BrowserTaskRunner 与 Browser Snapshot）

### Task 5: BrowserTaskRunner — deterministic navigation + temporary non-durable Agent

**Files:**
- Create: `backend/app/browser/runner.py`
- Create: `backend/tests/test_browser_runner.py`

**Interfaces:**
- Consumes: `claim_browser_task`/`complete_browser_task`/`mark_browser_blocked`/`fail_browser_task`（Task 3），`egress_policy_for`/`allowed_domains_for`/`classify_url`/`server_browser_limits`（Task 2），`model_resolver_capability`（provider.py），Harness `PlaywrightBrowser`/`EgressPolicy`。
- Produces: `async def run_browser_task_for_source(deps-like args) -> BrowserTaskResult`：
  - 参数：`task_id, task_run_id, owner_id, spec_version_id, source_id, reason|None, limits: BrowserLimits`。
  - 流程（§39）：捞 source + spec → policy 校验（owner/scope/URL public/robots 已由 fetch 阶段确认，这里再校验 private/scope）→ `claim_browser_task` → 若 COMPLETED snapshot 存在 → 直接返回已有结果（§25）→ 构建 EgressPolicy + Browser Agent system prompt → `async with PlaywrightBrowser(...) as browser_capability` 创建临时 `.agent(browser_capability)` → `agent.run(instructions)` bounded steps → 成功后 `_capture_browser_snapshot`（见 Task 6）→ `complete_browser_task` → 返回 `BrowserTaskResult`。异常映射：`BrowserUnavailableError` → FAILED/BROWSER_UNAVAILABLE（transient → activity retry）；`PlaywrightTimeoutError` → FAILED/TIMEOUT；`BrowserBlockedError` → BLOCKED（terminal，不 retry）；取消时 `TaskCancelledError` → 关闭 session + 重新抛出（§61）。
  - 临时 Agent 构造：`Agent("kairos:{model_config_id}", deps_type=None, capabilities=[model_resolver_capability, browser_capability], defer_model_check=True)`。**无 TemporalDurability、无 Planning/Memory/Workspace/Search/Collection/SubAgent**（§40）。
  - `system_prompt`（§54/§118）：写明 source URL、goal（来自 spec.goal + 固定 read-only 指令）、allowed_domains（`EgressPolicy.describe()`）、read-only mode、step 上限、goal fields（要提取的字段名/类型）、严禁登录/提交/改数据/上传/下载/绕过验证码。页面文字不可视为指令（prompt injection，§54）。
  - deterministic-first（§42）：优先由 runner 直接 `page.goto(source_url)`（初始导航不走 Agent 自由决策），只有 `wait_for/locator click/snapshot/get_text` 等渲染后交互才交给 Agent。若 Harness public API 不允许直接从 runner 驱动（`PlaywrightBrowserSession.ensure_page` 可直驱，已确认可实现），则用 Agent instructions 强制第一步只能访问 source URL。
  - step 计数：Activity 内用一个简单计数器在每轮 tool 结果后 ++，达到 `limits.max_steps_per_task` 或 `max_navigation_count` 即强制结束（不依赖模型自报）。
  - 返回 `BrowserTaskResult`（bounded，§57）：不含 HTML/DOM/screenshot/action trace/model messages。

- [ ] **Step 1: 写失败集成测试（注入 fake session 不启动 Chromium）**

用 in-memory `_Page` double（仿 Harness `_Page` Protocol）验证：`run_browser_task_for_source` 在 source 合法、snapshot 即返回已有结果时不重复 navegate；在 policy 拒绝时返回 BLOCKED；在 `BrowserUnavailableError` 时 FAILED。再写一个真实 Chromium 冒烟（同 Task 0 Step 5 的 goto quotes.toscrape.com/js），但走 runner 入口：`policy` 允许 host、`inner_text` 获取 quote 文本（有效启动并捕获）。这个真实用例在 CI 外标记 `@pytest.mark.integration` 且只在本地 infra 存在时跑。

- [ ] **Step 2: 跑测试到失败**

- [ ] **Step 3: 实现 runner.py**

在 `backend/app/browser/runner.py` 写 `BrowserBlockedError`、`run_browser_task_for_source` 主流程、`_build_browser_instructions(...)`、`_count_steps` 等。用 `activity.info()`/heartbeat（§60）在 `browser launched/step N/captured` 时 `activity.heartbeat()`（payload 只含 task id/attempt/step）。整个 Chromium 生命周期都在本 Activity。

- [ ] **Step 4: 跑 runner 测试 + 真实 Chromium 冒烟**

Run: `uv run --project backend pytest backend/tests/test_browser_runner.py -m "not integration" -q`
然后手动 `uv run --project backend python backend/tests/test_browser_runner.py`（integration 冒烟）——需 Chromium 在 PATH（Task 0 已装）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/browser/runner.py backend/tests/test_browser_runner.py
git commit -m "feat(browser): add isolated non-durable browser task runner"
```

### Task 6: Browser Snapshot 捕获与持久化（snapshot.py）

**Files:**
- Create: `backend/app/browser/snapshot.py`
- Create: `backend/tests/test_browser_snapshot.py`

**Interfaces:**
- Consumes: `PlaywrightBrowserSession`(Harness，`page.inner_text`/`page.content()` 若可用/`page.screenshot()`)，`MinioS3ObjectStore`，`persist_page_snapshot`（repositories），`browser` MinIO key 约定（§71）。
- Produces:
  - `def browser_snapshot_object_keys(owner_id, task_id, content_hash) -> SnapshotObjectKeys`：key 形式 `browser/{owner_id}/{task_id}/{content_hash}/rendered.html`（若获取到 render html）、`.../text.txt`、`.../screenshot.png`。若获取不到 rendered HTML，用 `raw_format` 标注（§68）。
  - `async def capture_page_state(page, store, owner_id, task_id) -> dict`：读取 `inner_text('body')`（跨 child frames 由 Harness 处理）、`page.url()`、`page.title()`、`page.screenshot(full_page=True)`（PNG bytes，存 MinIO，绝不返回/入 History）。返回 `{text, url, title, screenshot_bytes, content_hash}`。若 `screenshot` 失败，只降级 text；若 text 为空，不节操。
  - `async def persist_browser_snapshot(*, source_id, owner_id, task_id, task_run_id, url, canonical_url, title, text, screenshot_bytes, parent_snapshot_id, capture_method='BROWSER') -> PageSnapshot`：写 MinIO raw/text/screenshot；调用 `persist_page_snapshot(..., content_type='text/html', raw_storage_key=..., text_storage_key=..., screenshot_storage_key=..., capture_method='BROWSER', parent_snapshot_id=..., rendered_at=now, bytes_read=..., text_chars=...)`。content_hash 用对 `raw` bytes 或 `text` bytes 的 SHA-256（与现有 `sha256(body)` 一致）。
  - `PageSnapshot` 扩展字段在仓库 `persist_page_snapshot` 透传。`record/evidence` 链路零改动（Evidence 从 `snapshot.text_storage_key` 读，Browser snapshot 文本已覆盖）。

- [ ] **Step 1: 写失败测试**

`test_browser_snapshot.py`：`browser_snapshot_object_keys` 返回 `browser/...` 前缀；`persist_browser_snapshot` 写 MinIO raw/text/screenshot 且 metadata 正确（用 `InMemoryObjectStore` 假对象）；`capture_state` 在 `_Page` double 返回 text/screenshot bytes。

- [ ] **Step 2: 跑测试到失败 → Step 3 实现 → Step 4 跑测试**

- [ ] **Step 5: 在 runner 里接入 `persist_browser_snapshot`**（Task 5 Step 3 的 runner 成功路径调用它）。再次跑 runner + snapshot tests。

- [ ] **Step 6: Commit**

```bash
git add backend/app/browser/snapshot.py backend/tests/test_browser_snapshot.py
git commit -m "feat(browser): persist browser-rendered snapshot to minio"
```

### Task 7: 事件 + 进度 + 完成判定升级（events.py / collection / repositories / activities / api）

**Files:**
- Create: `backend/app/browser/events.py`
- Modify: `backend/app/domain.py`（Progress/Decision 已在 Task 1 加）
- Modify: `backend/app/repositories.py`（progress counts、source transition、completion budget）
- Modify: `backend/app/activities.py`（new activities 供 workflow 调用）
- Modify: `backend/app/api.py`（progress/search-rounds/spec 返回 browser 字段）
- Create: `backend/tests/test_browser_events.py`
- Create: `backend/tests/test_browser_completion.py`

**Interfaces:**
- Produces:
  - `async def emit_browser_event(deps-like, event_type, summary, payload) -> None`：复用 `insert_agent_event` + `bounded_payload` + `EventEnvelope(agent_name=... )`。事件类型（§85）：`browser.required`(source_id, reason)、`browser.started`(attempt)、`browser.navigation`(domain)、`browser.action`(attempt/step_number/action_type/short_description/current_domain)、`browser.snapshot.created`(snapshot_id/source_id)、`browser.completed`(attempt/snapshot_id)、`browser.failed`(attempt/failure_code)、`browser.blocked`(attempt/failure_code)。`browser.action` 按 attempt 计数 + 超 `max_action_events` 聚合（§90）。
  - `get_collection_progress` 增加 browser 计数（从 `browser_tasks` 统计 + 从 sources 算 `browser_required_sources`）。
  - `evaluate_collection_completion` 调用处传 `browser_required_sources`/`browser_tasks_remaining`（Task 1 已定义签名）。
  - `activities.py` 新增：`load_browser_task_activity`, `complete_browser_task_activity`, `fail_browser_task_activity`, `mark_browser_blocked_activity` 等（lean，仅做 owner-scoped 调用）。

- [ ] **Step 1: 写失败测试**

`test_browser_events.py`：`emit_browser_event` 产生正确 EventEnvelope 且 payload bounded（无 HTML/no screenshot bytes）；`browser.action` 计数与聚合（>max 时不再逐条，产生聚合）。`test_browser_completion.py`（复用 §111 A–D + §81/82/83）：BROWSER_REQUIRED 存在+budget → CONTINUE；budget exhausted → PARTIALLY_COMPLETED/BROWSER_TASK_LIMIT；blocked → (search budget remains) CONTINUE；browser 成功 target → COMPLETED；browser_required_sources in progress actionable_browser_sources ≤ 5。

- [ ] **Step 2: 跑测试到失败 → Step 3 实现 → Step 4 跑测试 + ruff**

- [ ] **Step 5: 更新 API**：`collection_progress_api`/`collection_search_rounds_api`/`snapshot_metadata_api` 直传新字段；新增（见 Task 8）screenshot & browser-task-status 路由。

- [ ] **Step 6: Commit**

```bash
git add backend/app/browser/events.py backend/app/repositories.py backend/app/activities.py backend/app/api.py backend/tests/test_browser_events.py backend/tests/test_browser_completion.py
git commit -m "feat(collection): surface browser progress and completion budget"
```

### Task 8: API — Screenshot 与 Browser Task 状态（owner-scoped）

**Files:**
- Modify: `backend/app/api.py`
- Modify: `backend/app/repositories.py`（加 `get_screenshot_storage_key`, `snapshot_belongs_to_task`, `get_browser_task_summary_for_source`）
- Create: `backend/tests/test_browser_api.py`

**Interfaces:**
- Requests:
  - `GET /api/tasks/{task_id}/snapshots/{snapshot_id}/screenshot`（§91）：owner check，`capture_method=BROWSER`，`screenshot_storage_key` 存在 → 从 MinIO 读 bytes → `Response(content=png_bytes, media_type="image/png")`。不返回 access key/secret/raw key/内部 endpoint。非 owner → 404。
  - `GET /api/tasks/{task_id}/browser-tasks`（§93/§25）：返回 owner-scoped `list[BrowserTaskSummary]`（无 HTML/DOM/screenshot）。
  - `snapshot_metadata_api` 增加 `capture_method`, `has_screenshot`, `parent_snapshot_id`（仅对前端安全的部分；不返回 `screenshot_storage_key`），若 `parent_snapshot_id` 非空则附带（§92）。
- Guard：所有路由 `_owner_id` + task/run owner 校验。

- [ ] **Step 1: 写失败测试**

owner A 不能读 owner B 的 screenshot；非 BROWSER snapshot → 404/400；screenshot bytes >0；metadata 有 capture_method=HTTP 默认；parent_snapshot_id 正确透传。

- [ ] **Step 2: 跑测试到失败 → Step 3 实现 → Step 4 跑测试 + ruff**

- [ ] **Step 5: Commit**

```bash
git add backend/app/api.py backend/app/repositories.py backend/tests/test_browser_api.py
git commit -m "feat(api): expose owner-scoped browser screenshot and task status"
```

## 后期任务（第 2 组 — 主 Agent 引导、回归、收口）

### Task 9: 主 Agent collection instructions 升级（browser escalation guidance）

**Files:**
- Modify: `backend/app/workflows.py`（`_collection_prompt`/`_continuation_prompt` 增加 Browser esc 引导）
- Modify: `backend/app/agent/runtime.py`（collection toolset instructions 更新；browser toolset instructions 已在 Task 4）
- Create: `backend/tests/test_browser_prompt.py`

**Interfaces:**
- Consumes: `CollectionSourceStatus.BROWSER_REQUIRED`、`browser_required_sources`/`actionable_browser_sources`。
- Produces: 更新后的 `_collection_prompt` 在 EXPLORATORY/SPECIFIED_SOURCE 分支各加一段：HTTP FIRST；`inspect_snapshot` 后若 visible text 过少/只有 app shell/必须 JS/内容隐藏 → 调 `request_browser_task(source_id, reason=...)`；成功后重新 `inspect_snapshot`（Browser snapshot），再 `commit_extraction`；禁止对 robots/private/403/captcha/unsupported 调 browser；禁止以空 records 跳过（§76/78）。`_continuation_prompt` 同样带上 browser 引导。

- [ ] **Step 1: 写失败测试**

断言 EXPLORATORY prompt 包含 `request_browser_task` 关键字、`HTTP FIRST` 语义；SPECIFIED_SOURCE 分支也含；`_collection_prompt` 输出的 `CollectionExecutionContext` 不携带 secrets/screenshot bytes。

- [ ] **Step 2: 跑测试到失败 → Step 3 实现 → Step 4 跑 workflow/collection/repositories 回归**

Run: `uv run --project backend pytest backend/tests/test_exploratory_workflow.py backend/tests/test_collection_completion_phase3b.py backend/tests/test_collection_dedup.py backend/tests/test_phase3b_api.py backend/tests/test_browser_prompt.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows.py backend/app/agent/runtime.py backend/tests/test_browser_prompt.py
git commit -m "feat(agent): add http-first browser escalation guidance"
```

### Task 10: Phase 3 回归 — 普通 HTTP Source 不触发 Browser

**Files:**
- Create: `backend/tests/test_phase3b_regression.py`

**Interfaces:**
- Consumes: 现有 factory/collection tests。
- Produces: 回归断言：
  - static page (specified source, HTTP adequate) 的 workflow 不出现 `request_browser_task` tool 调用（`observed_tool_sequence` 不含 browser）。
  - `browser_required_sources=0` 时 `evaluate_collection_completion` 行为与 Phase 3B 完全一致（CONTINUE/COMPLETED 不因 browser 字段改变）。
  - 404/410/robots/auth/captcha source 不产生 `browser_hint=LIKELY_JS_REQUIRED`（§114）→ 实际用 fetch_source 结果驱动，不 browser。

- [ ] **Step 1: 写测试 → Step 2 跑（应直接 PASS，作为回归基线）→ Step 3 commit**

```bash
git add backend/tests/test_phase3b_regression.py
git commit -m "test(browser): guard phase3b http-first regression"
```

### Task 11: P4-F 真实 Browser Vertical Slice（权威 smoke）

**Files:**
- Create: `tests/smoke/specified_source_browser_smoke.py`
- Modify: `tests/smoke/__init__.py`（如需要 import）

**Interfaces:**
- Consumes: 真实 DeepSeek `kairos-agent-v1`、真实 `PlaywrightBrowser`/Chromium、MinIO、Temporal、PostgreSQL；`quotes.toscrape.com/js/`（§96）。
- Produces: 权威 P4-F vertical slice，返回 JSON（对应 §137 报告字段）：`workflow_id`, `browser_tasks[*]`, `browser_snapshot_id`, `capture_method`, `screenshot_verified: bool`, `rendered_text_verified: bool`, `records[*].PASSED/NEEDS_REVIEW`, `evidence_verified: x/y`, `browser_backed_evidence: z`, `temporal_history_bytes`, `full_browser_html_in_history: NO`, `screenshot_binary_in_history: NO`, `browser_child_history_in_main: NO`, `secrets_in_history: NO`, `sse_events[*]`, gates `P4-A..P4-F`。
- Flow（§99）: `create task (SPECIFIED_SOURCE)` → confirm spec（seed=quotes.toscrape.com/js/，fields `quote:STRING required` + `author:STRING required`，target_count=1）→ start workflow (prompt 引导 HTTP first / browser escalation) → SSE wait → query task/records/evidence/snapshot → MinIO screenshot exists >0 → Temporal history byte scan（无 base64/binary/HTML全文/browser child messages/secrets）→ 打印终态 JSON（exit 0）。
- 关键断言（§100/§101/§102）：每条 PASSED 记录每个 required field 都有 `FieldEvidence.verified=true` 且 `FieldEvidence.snapshot_id == browser_snapshot_id`；quote 在 browser clean text 中出现（服务器 quote matching 对 browser text 执行）；screenshot exists >0；History 无 `base64`、无 `data:image/png`、无 `<html`、无 child tool 全量、无 credential marker。

- [ ] **Step 1: 写 smoke 脚本（TDD 先从失败 DTO 开始，但不强制先行）**
- [ ] **Step 2: 只跑一次真实 smoke（§119）**：先确保 infra 健康（Task 0 已装 Chromium）。Run:
```bash
DEEPSEEK_API_KEY=... TAVILY_API_KEY=... uv run --project backend python tests/smoke/specified_source_browser_smoke.py
```
Expected: exit 0 + 终态 JSON 含 `Screenshot: verified`、`Rendered text: verified`、`P4-A..P4-F: PASS` 全字段。若发现明确 bug → fix → 再跑一次（最多 2 次完整真实 smoke）。若 fixture 失效 → 按 §133 换一个等价公开 JS 页面（最多一次）。
- [ ] **Step 3: 持久化终态 JSON**：`docs/audits/phase4-browser-smoke-2026-09-03.json`
- [ ] **Step 4: smoke 后 infra marker 再检查**（ExitCode/OOMKilled/RestartCount），确认无 Chromium 孤儿进程（§120/121）。
- [ ] **Step 5: Commit**

```bash
git add tests/smoke/specified_source_browser_smoke.py docs/audits/phase4-browser-smoke-2026-09-03.json
git commit -m "test(browser): verify real browser escalation vertical slice"
```

### Task 12: 前端最小 surface（snapshot 元数据 + screenshot）

**Files:**
- Modify: `frontend/src/api.ts`（`SnapshotMetadata` DTO + `getSnapshotScreenshot`）
- Modify: `frontend/src/App.vue`（Evidence/Snapshot 区显示 `Capture Method: HTTP/Browser` + `Screenshot: View` 链接，当 `has_screenshot && capture_method==='BROWSER'`；BrowserTask 状态若可得显示）
- Create: `frontend/src/__tests__`（如已有测试基础设施则加 1-2 个类型/渲染测试，否则跳过并说明）

**Interfaces:**
- Consumes: `SnapshotMetadataResponse`（capture_method/has_screenshot/parent_snapshot_id）、`GET .../screenshot`。
- Produces: 最小 UI（§93）。不重做页面、不做 live Chromium/DOM viewer/network inspector（§94）。

- [ ] **Step 1: 更新 `api.ts` DTO + `typecheck`** → Step 2 更新 `App.vue` → Step 3 `npm --prefix frontend run typecheck` +（必要时）`npm --prefix frontend run build` → Step 4 commit
```bash
git add frontend/src/api.ts frontend/src/App.vue
git commit -m "feat(web): expose browser snapshot metadata and screenshot"
```

### Task 13: 全量收口门禁

**Files:**
- Modify: `backend/app/browser/__init__.py`（导出领域，干净）
- 可能 modify `backend/pyproject.toml` 无依赖变化

**Interfaces:**
- Produces: 最终证据集合（对应 §137 报告模板）。

- [ ] **Step 1: 全后台 targeted tests**（§122）：除 integration 外跑 browser 相关 + Phase 3 回归
Run: `uv run --project backend pytest backend/tests/test_browser_*.py backend/tests/test_phase3b_regression.py backend/tests/test_phase3b_api.py -m "not integration" -q`

- [ ] **Step 2: migration 再次升级**
Run: `uv run --project backend alembic -c backend/alembic.ini upgrade head` Expected: head=`0004_phase4_browser_escalation`

- [ ] **Step 3: ruff 检查本轮变更文件**
Run: `uv run --project backend ruff check backend/app/browser backend/tests/test_browser_*.py backend/tests/test_phase3b_regression.py backend/app/agent/browser_tool.py backend/app/agent/runtime.py backend/app/api.py 2>&1 | tail -5` Expected: All checks passed.

- [ ] **Step 4: 前端 typecheck + build（若 Step 12 改了 vue）**

- [ ] **Step 5: 确认每个 Gate 证据并写最终报告文本**（P4-A..P4-F PASS + PRODUCTION section per §137）

- [ ] **Step 6: 最终 Commit（如还有未提交变更）**

---

## Self-Review Checklist

检查本 plan 是否违反硬约束：

1. **有没有直接把 PlaywrightBrowser 挂到 durable main Agent？** NO — P4-A 架构图（§Global Constraints）明确 `kairos-agent-v1` 不含浏览器 capability；`PlaywrightBrowser` 只存在于 `BrowserTaskRunner` 内临时 Agent（Task 5/6）。`kairos-browser-v1` 只暴露 `request_browser_task`，不暴露 navigate/click 等低层工具（Task 4）。
2. **有没有创建第二套 Kairos Agent？** NO — 唯一 `kairos-agent-v1`；Browser 子 Agent 是 runner 私有临时对象，无 TemporalDurability，不 Search/不写 Record/不做 Dedup/不判 Completion/不持 CollectionSpec，Activity 结束销毁（Task 5、§2/40）。
3. **Browser 是否能访问任意 URL？** NO — `allowed_domains`（source hostname + scope）经 `egress_policy_for` 限制，`block_private_addresses=True`，redirect 经 `request_with_safe_redirects`（Task 2、§49-52）。`request_browser_task` 只接收 `source_id`，URL 服务端解析（§36）。
4. **Browser 是否可以访问 localhost/private network？** NO — Harness block_private 默认 + Kairos `validate_public_http_url` + `classify_url` + 显式拒绝（Task 2/5、§51-53、§106）。
5. **Browser 是否能登录、提交表单或修改远程状态？** NO — read-only 政策（Task 2 denylist）+ 无 type/fill/login express（§37/47-48）+ system instructions（Task 5，§54）+ Browser Agent 生命周期 Activity 内（§58）；登录/表单/提交 → 工具缺或 policy 拒绝。
6. **Browser screenshot/DOM 有没有进入 Temporal History？** NO — screenshot PNG 直接写 MinIO，只存 storage key/bytes_ref；PageSnapshot metadata 只存 URL/hash/keys；`BrowserTaskResult` bounded 无 screenshot/DOM/model messages（Task 6/8、§17/70/72/102-103）。
7. **是否重新实现 PageSnapshot？** NO — 扩展现有 `PageSnapshot`（加 capture_method/parent/screenshot/rendered_at），minio key 复用 `snapshot_object_keys` + browser 前缀（Task 1/6、§30-31）。
8. **是否重新实现 Extraction/Evidence？** NO — 复用 `commit_extraction`/`FieldEvidence`/`validate_record_submission`（quote 对 snapshot text 匹配），Browser snapshot 只是新的 snapshot_id（§74-75、§100）。
9. **是否提前实现 Phase 5 Continue-As-New？** NO — 明确禁止（Task 13 回归断言、§136）。
10. **是否提前实现 BrowserUse？** NO — 只使用 Harness `PlaywrightBrowser`（§4、§135）。

发现任何问题 → 自行修正后继续执行。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-02-kairos-phase3b-closure-phase4-browser.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task + review between tasks.
**2. Inline Execution** — execute tasks in this session with checkpoints.

Given the hard external Gate and real-provider/Chromium integration nature of Phase 4, **Inline Execution** is the pragmatic choice here (single working tree, iterative real smoke). Proceeding inline with executing-plans discipline: one task at a time, targeted tests per task, frequent focused commits.