# M-10 模块执行记录

状态：**DONE_LOCAL**（2026-08-11）
负责人/Agent：Claude Code
Baseline（M-09 DONE_LOCAL）SHA：`5830063`（docs(discovery): finalize M-09 commit table）
分支：`feature/M-10-fetch-snapshot`（pushed：NO）
依赖模块：M-03（CredentialVault）、M-04（PageSnapshot/Checkpoint/Idempotency）、M-07（TaskWorkflow/SSE）、M-08（Node Registry/Approval）、M-09（Frontier/AccessDecision/robots/SSRF）
目标环境：local（M-10 不部署；下一强制 Gate 为 M-09～M-12 后的 DEPLOY-GATE-3）

## 1. 模块目标
实现 D-009 固定能力升级阶梯（structured/静态 HTTP → Scrapy 批量 → 证据驱动 Playwright →
Browser Agent 仅契约），把每次网页获取变成可审计、可恢复、幂等的 immutable PageSnapshot，
并完成网站凭据（Cookie / Username-Password）+ M-08 Approval 的受控访问，交付 M-11 可消费的
`PageSnapshotRef` Handoff 契约。

## 2. 契约
- `app/crawling/contracts.py`：`FetchRequest`（extra=forbid，禁 Cookie/password/Authorization）、
  `FetchResult`（status/tool/http/snapshot/escalation/retry/error）、`PageSnapshotRef`
  （snapshot_id/content_hash/storage_ref/final_url/tool/version/spec/run）、
  `EscalationEvidence`（EMPTY_BODY/DYNAMIC_APP_SHELL/JS_RENDER_SIGNAL/KEY_FIELDS_MISSING/
  INTERACTION_REQUIRED/SITE_STRATEGY_BROWSER）、`redact_headers`（allowlist 脱敏）
- `app/crawling/errors.py`：`FetchErrorCode`（TIMEOUT/DNS/CONNECTION/RATE_LIMITED/SERVER_ERROR/
  NOT_FOUND/AUTH_REQUIRED/ACCESS_DENIED/CAPTCHA_REQUIRED/UNSUPPORTED_RESPONSE/EMPTY_CONTENT/
  DYNAMIC_RENDER_REQUIRED/CREDENTIAL_REQUIRED/STORAGE_ERROR/...）+ `RETRYABLE_CODES`
- `app/crawling/http_fetch.py`：`SafeFetchHttp`（复用 M-09 SSRF 守卫 + 每跳重定向复验；
  body/size cap/timing/redirect chain/header allowlist）
- `app/crawling/content.py`：`classify_content`（structured/static/empty/js shell，文本标记+可读正文判定）
- `app/crawling/snapshot.py`：`PageSnapshotService`（content-addressable Blob 复用 +
  snapshot_version 链 + 对象先写/DB 幂等恢复）
- `app/crawling/repository.py`：`PageSnapshotRepository` / `SiteFetchStrategyRepository`（owner-safe）
- `app/crawling/fetch_executor.py`：`FetchNodeExecutor`（静态层；证据升级标记；401/403/404/captcha 分类）
- `app/crawling/batch.py`：`ScrapyBatchFetcher`（Scrapy 语义批量；复用同一安全路径，有界并发）
- `app/crawling/browser.py`：`PlaywrightChromiumRenderer` + `BrowserRenderNodeExecutor`（证据门禁；
  `BROWSER_AGENT_REQUIRED` 仅契约）
- `app/crawling/site_strategy.py`：`SiteStrategyService`（TTL/失效重探测/连续失败失效；不越权）
- `app/crawling/credentials.py`：`WebsiteCredentialService`（Cookie / Username-Password，
  CURRENT_TASK / SAVED_DOMAIN；vault 加密；脱敏 metadata；明文仅执行期瞬态）
- `app/activities/credential_approval.py`：`resolve_credential_access` activity
- `app/api/routes/credentials.py` + `app/api/schemas.py`：凭据存储/列表/删除 API（无明文回读）
- `app/crawling/executors.py`：`install_fetch_executors()`（只注册 FETCH / BROWSER_RENDER）
- `app/workflows/task_workflow.py`：新增 `CREDENTIAL_REQUIRED` 等待分支
- Migration：`0008_extend_page_snapshot_fetch.py`

## 3. 行为
- Fetch 只消费 `READY_FOR_FETCH` + AccessDecision=ALLOW；robots/scope/SSRF 每次重新校验。
- 能力阶梯：structured/静态 HTTP 成功 → PageSnapshot → FETCHED（不启动 Playwright）；
  空/JS shell → 保留 HTTP attempt snapshot + `EscalationEvidence` → BROWSER_PENDING →
  BrowserRender（Playwright）→ rendered PageSnapshot（prior 指向 shell，升级链审计）。
- 401 无凭据 → CREDENTIAL_REQUIRED → URL WAITING_CREDENTIAL + Chat“需要凭据”卡片；
  用户存凭据 → credential_access Approval → approve → resolve_credential_access consume 复验
  → READY_FOR_FETCH → Fetch 带 Cookie/Basic Auth 重抓。403/404/captcha 分类处理，不盲升级。
- Scrapy 批量：有界并发（Semaphore）、失败隔离、robots 不越权、结果统一 FetchResult/PageSnapshot。
- SiteFetchStrategy：同站成功写策略（TTL）；第二同站 URL 优先 browser；失效/结构变化重探测；
  策略只决定工具，不绕过 AccessDecision。
- Credential：CURRENT_TASK 只能本任务引用；SAVED_DOMAIN 供同用户后续任务（仍须 Approval）；
  domain 范围检查（a.com Cookie 不发 b.com）。
- PageSnapshot：immutable、content-addressable（同内容复用 Blob + snapshot_version 链 + prior 关联）、
  owner-safe（user 前缀 key + ownership 查询）。

## 4. 明确不做（M-11+）
字段提取 / JSON-LD/CSS/XPath/LLM extractor / FieldEvidence / Record / Normalize / Deduplicate /
Quality / CSV。Browser Agent 自主点击/填表/验证码处理。M-16 资源池调度。不新增页面
（Credential Drawer / Settings 安全区复用既有 Overlay/页面）。不部署 Staging（DEPLOY-GATE-3 NOT_REACHED）。

## 5. 验收证据
### scoped tests
```bash
.venv/Scripts/python.exe -m pytest tests/crawling -q
# 40 passed：contracts / snapshot / http_fetch / fetch_e2e_static（静态全链 + renderer=0）/
#   fetch_e2e_dynamic（HTTP→证据→Playwright→rendered）/ fetch_e2e_credential（Cookie E2E +
#   Username-Password contract + 跨域不泄漏）/ fetch_e2e_failure（503→200 有界重试；401/403 不盲升级；
#   captcha 不绕过）/ batch_fetch / browser_render / site_strategy / content_hash_reuse /
#   website_credentials / executor_binding
.venv/Scripts/python.exe -m pytest tests/crawling/test_playwright_real.py -q
# PASS：真实 chromium 渲染本地 JS fixture（chromium 已安装）
.venv/Scripts/python.exe -m pytest tests/integration/test_m10_fetch_workflow.py tests/integration/test_m09_discovery_workflow.py -q
# 收集通过（3 skipped）；2 条 Temporal 场景本地栈未启动跳过（与 M-09/M-08 先例一致）
```
四类 E2E fixture：1) 静态 HTTP 全链 + Playwright invocation=0；2) 动态 HTTP→证据→Playwright→rendered；
3) Cookie 凭据全链（401→Drawer→Approval→resolve→带凭据抓取）+ Username/Password contract；
4) 失败/重试（503→200 有界重试、401/403 不盲升级、captcha 不绕过）。

### ruff / mypy / secret scan
```bash
.venv/Scripts/python.exe -m ruff check app tests      # PASS
.venv/Scripts/python.exe -m mypy app                  # PASS（132 files）
# secret scan：固定测试 secret 在 app/ alembic/ 出现次数 == 0；app/main + worker 导入 OK
```

### Migration
```bash
.venv/Scripts/python.exe -m alembic heads   # 0008 (head)
.venv/Scripts/python.exe -m alembic upgrade head --sql   # 0008 page_snapshots/url_resources/site_fetch_strategies/credentials 生成
```

## 6. Git 证据（feature/M-10-fetch-snapshot，基线 5830063，pushed NO）
| Commit | 内容 |
|---|---|
| c27ef55 | feat(fetch): add typed fetch/snapshot contracts and page snapshot persistence（migration 0008） |
| f413420 | feat(fetch): add static http and structured fetch executor |
| 5561328 | feat(fetch): add scrapy-style bounded batch fetch executor |
| e90daa1 | feat(browser): add evidence-driven playwright rendering（playwright 依赖 + 真实 chromium 验证） |
| e41eef2 | feat(credential): connect website credentials and approval flow |
| cd7a418 | feat(workflow): bind fetch and browser executors with site strategy and sse |
| 07b1046 | feat(ui): wire credential drawer and saved website credentials |
| （docs） | docs(fetch): record M-10 execution（本记录） |

## 7. 跨模块联动结果
- 上游 M-03 CredentialVault：PASS（WebsiteCredentialService 复用 vault 加密，无第二套 Secrets DB）
- 上游 M-04 PageSnapshot/Checkpoint/Idempotency：PASS（PageSnapshot 扩展 + observation 链 + 幂等恢复）
- 上游 M-07 TaskWorkflow/SSE：PASS（CREDENTIAL_REQUIRED 分支 + fetch.* SSE 事件）
- 上游 M-08 Node Registry/Approval：PASS（只注册 FETCH/BROWSER_RENDER；credential_access 复用 ApprovalService）
- 上游 M-09 Frontier/AccessDecision/robots/SSRF：PASS（只消费 READY_FOR_FETCH；每 URL 重新校验）
- 下游 M-11 Handoff：`PageSnapshotRef` 稳定指向 immutable stored content + fetch metadata + tool/hash

## 8. 完成结论
**M-10 = DONE_LOCAL**。下一阶段：M-11（不要开始）；DEPLOY-GATE-3 在 M-09～M-12 完成后（NOT_REACHED）。
