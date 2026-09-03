# Kairos Browser Escalation Architecture

> Phase 4 决策记录与边界依据（2026-09-03 封板）。本文档记录 Harness 源码结论、架构边界、
> 安全约束与 Gate 证据，供后续维护与 Phase 5 参考。不重复任何产品决策日志细节。

## 1. 为什么 durable main Agent 不能直接挂 PlaywrightBrowser

源码结论（上游 `pydantic-ai-harness`，未 patch）：

- `PlaywrightBrowser.for_agent()` 遍历 `agent.root_capability` 的所有 sibling，
  若任何一个 `isinstance(sibling, BaseDurabilityCapability)` 就在 **Agent 构造期** raise
  `UserError('PlaywrightBrowser does not support durable execution …')`。
- 检测用 `BaseDurabilityCapability`（Temporal/DBOS/Prefect 共享基类），Pydantic AI 没有
  公开 durable tier marker；`innermost` 排序位置不可靠（`InputGuard` 也声明它）。
- 本质原因：Temporal durability 把 tool call 重放成 Activity，live Chromium page 无法
  在 Activity 边界或 worker 重启间存活。

因此 `Agent(capabilities=[TemporalDurability(...), PlaywrightBrowser(...)])` 被拒绝是
**设计保证**，不是巧合。本地验证：`backend/tests/test_browser_harness_boundary.py`
断言该组合构造即 raise。

## 2. Kairos 解决方案：Browser Activity 内的临时 NON-DURABLE Agent

```text
kairos-agent-v1 (durable)
        │  request_browser_task(source_id)   ← 唯一 Browser 业务工具 (§35/36)
        ▼
kairos-browser-v1 FunctionToolset
        │  TemporalDurability → request_browser_task 成为独立 Activity
        ▼
BrowserTaskRunner (Activity 内)
        ├─ validate_public_http_url 预检（私网/非 http 启动前拒绝，§79）
        ├─ claim_browser_task (task_run_id, source_id 唯一，§25)
        ├─ PlaywrightBrowserSession(policy=EgressPolicy(…))   ← 真实 Chromium 完全在此
        ├─ page.goto(source_url) deterministic-first（§42，不交给模型选 URL）
        ├─ 临时 Agent(capabilities=[ResolveModelId])   ← 无 TemporalDurability
        │     toolsets=[FilteredToolset(read-only browser tools)]
        │     usage_limits=UsageLimits(tool_calls_limit=…)   ← step 上限
        ├─ capture_page_state → persist_browser_snapshot
        ├─ BrowserTask COMPLETED + CollectionSource FETCHED
        └─ session __aexit__   ← Chromium 必然释放
```

- 临时 Agent：`Agent(..., capabilities=[model_resolver_capability], deps_type=KairosAgentDeps)`，
  只有 `model_config_id` 等非 secret 字段（§56/§116）。
- 工具滤波 `FilteredToolset(filter_func=_only_readonly_tools)` 只允许
  `ALLOWED_BROWSER_TOOLS={navigate,snapshot,get_text,scroll,wait_for,click,hover,screenshot}`；
  默认拒绝 `type_text/press_key/select_option/execute_js/tabs/go_back/go_forward/
  handle_next_dialog/network_requests/console_messages`（§43/44/47）。
- 模型 resolver 复用 `ModelResolver` + 当前 DeepSeek；不因浏览器引入新 Provider（§134）。
- `activity.in_activity()` + 模块级 `activity.heartbeat()`（注意：`activity.info()` 的
  Info 对象 **没有** heartbeat 方法 —— 这是真实踩过的坑，见 `fix(browser)` commit）。

## 3. Egress 与只读边界（Security）

- `EgressPolicy(allowed_domains=[source_host + scope], block_private_addresses=True,
  include_subdomains=True, allowlist_reach={'navigation','data'})`
  （Harness 默认 reach 即 navigation+data；subnet/私网由 policy 拦截）。
- **Harness 限制**（源码明确）：它提供路由层 guard（navigation/fetch/XHR/WebSocket 的
  私网与 allowlist 拦截），但 **不是完整安全边界** —— DNS rebinding 未闭合
  （Chromium 会再次解析）、frame 内 data 请求需 allowlist、`sendBeacon` 类通道。
  生产 Sandbox/网络 egress 仍是 Phase 5/Production 安全要求（§52）。
- Kairos URL 策略（`validate_public_http_url` / `request_with_safe_redirects`）在
  **Chromium 启动前**服务端校验 initial URL 与 redirects（§52A）。
- 领域层不 import Playwright；`BrowserTaskRunner` 是模块私有实现细节。

## 4. Domain 模型（§24-27）

- `BrowserTask(id, owner, task, run, spec, source, status, attempt_count, snapshot,
  policy_version, failure, times)`，唯一键 `(task_run_id, source_id)`。
- `BrowserTaskStatus = PENDING | RUNNING | COMPLETED | FAILED | BLOCKED`。
- `BrowserFailureCode`：AUTH_REQUIRED / CAPTCHA_REQUIRED / ACCESS_DENIED / ROBOTS_BLOCKED /
  OUT_OF_SCOPE / UNSUPPORTED_INTERACTION / READ_ONLY_POLICY_BLOCKED / BROWSER_UNAVAILABLE /
  TIMEOUT / TRANSIENT_RETRY / INTERNAL。
- `CollectionSource` 增加 `BROWSER_REQUIRED`；`PageSnapshot` 增加
  `capture_method(HTTP|BROWSER) / parent_snapshot_id / screenshot_storage_key / rendered_at`。
- 状态迁移：PENDING→FETCHED；HTTP 足够→PROCESSED；需 Browser→BROWSER_REQUIRED；
  成功→FETCHED（snapshot 更新为 Browser 快照）→PROCESSED；业务阻塞→BLOCKED。
- 同一个 source 一个 run 只有一个正式 BrowserTask；COMPLETED + snapshot 存在再次
  request 直接返回已有结果，绝不重新开 Chromium（§25/§109/§110）。

## 5. Completion / Progress / Events

- `evaluate_collection_completion` 增加 `browser_required_sources/browser_tasks_remaining`：
  target 未达 + BROWSER_REQUIRED + budget → CONTINUE；budget 耗尽 →
  `PARTIALLY_COMPLETED/BROWSER_TASK_LIMIT`；blocked + search budget → CONTINUE；
  浏览器成功达 target → COMPLETED（§81-83）。
- `get_collection_progress` 返回 bounded：`browser_required_sources/tasks_used/remaining/
  completed/blocked/failed/actionable_browser_sources(≤5)`（§80/84）。
- 领域事件：`browser.required/started/navigation/action/snapshot.created/completed/
  failed/blocked` 经既有 `agent_events + SSE`（子 Agent 绝不直接推网络，§86）；
  `browser.action` 超 `max_action_events` 聚合（§90）；payload 严格有界。

## 6. Temporal 边界

- 真实 Chromium 生命周期完全封闭在 `agent__kairos-agent-v1__toolset__kairos-browser-v1
  __call_tool` Activity 内；Workflow 永远看不到 live Browser（§58）。
- Heartbeat payload 只含 attempt/step/小状态（§60）；DOM/HTML/screenshot 永不进 History
  （§102/131 实测：P4-F 成功 run History 仅 456KB）。
- Browser Activity 独立 config：`start_to_close=300s`、`retry = 2`（transient 新 session）。
- 取消：`CancelledError` → session context manager 释放 Chromium（§61）。
- 禁止 Phase 5：Continue-As-New / Pause-Resume / Workflow Versioning 本轮不做（§136）。

## 7. 证据（2026-09-03 本地全绿）

- 真实 P4-F 竖切（`tests/smoke/specified_source_browser_smoke.py`）exit 0，
  终态 JSON 存 `docs/audits/phase4-browser-smoke-2026-09-03.json`。
- BrowserTask COMPLETED（attempts=1）；Browser snapshot `capture_method=BROWSER`；
  screenshot 100KB PNG owner 可读；rendered clean text 含 quote；
  Evidence 1/1 verified 且 quote 匹配 browser 文本；deterministic 校验正确把缺
  author evidence 的记录判为 NEEDS_REVIEW（管道非缺陷）。
- Temporal History 只有 456,219 bytes；无 full HTML / screenshot binary / secrets /
  browser child history。
- 真实 Chromium 集成测试（`test_browser_runner.py::test_runner_real_chromium_*`）通过，
  无孤儿 Chromium 进程。
- 迁移 `0004_phase4_browser_escalation` 在 PostgreSQL `upgrade head` PASS。