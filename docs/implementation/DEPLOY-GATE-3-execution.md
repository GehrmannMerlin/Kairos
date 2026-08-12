# DEPLOY-GATE-3 执行记录：真实网页采集 E2E Staging

状态：**PASS_FAST_DEV**（2026-08-12）— Golden Path A（指定来源静态）**PASSED**；Golden Path B（探索式 Search）**PASSED**；Golden Path C（动态 Playwright）**DEFERRED_FAST_DEV**（FAST DEVELOPMENT WAIVER，不再作为 M-13 前置门禁）
负责人/Agent：Claude Code
Staging：https://staging.kairos.ac.cn（服务器 47.238.145.24）
当前运行镜像：`kairos-{api,worker}:staging-4306c65` + `kairos-{web}:staging-f0edd653fca5`（本轮未重新部署；仓库代码已回退到 `6299223`，下次 M-13 部署时以回退后代码重建镜像）
Migration：`0010`（未变，无 schema 变更）

## 1. 最终门禁状态

| 路径 | 状态 |
|---|---|
| Golden Path A（指定来源静态） | **PASS** |
| Golden Path B（探索式 Tavily Search → Crawl → Extract → Validate） | **PASS** |
| Golden Path C（真实动态页面 HTTP → Playwright E2E） | **DEFERRED_FAST_DEV** |
| Deferred reason | `DYNAMIC_PLAN_GENERATION_UNSTABLE` |
| 技术债 | `DEFERRED-DYNAMIC-E2E-01`（见 §8） |
| **DEPLOY-GATE-3** | **PASS_FAST_DEV**（FAST DEVELOPMENT WAIVER，用户 2026-08-12 批准） |
| M-12 | **DEPLOYED** |
| M-13 | **UNBLOCKED** |

## 2. 已经 PASS 的项目（真实结果）
- M-12 本地实现 + scoped tests + migration 0010 upgrade/downgrade SQL 验证
- Staging 部署：镜像构建/传输/compose up 成功，API healthy，worker 加载全部 executor
- HTTPS / /api/health/live / /api/health/ready：全部 PASS（postgresql/temporal/object_storage ok）
- Migration 0010：已应用
- Provider 配置：gate3.a@kairos.test（user 53）DeepSeek `deepseek-chat` AVAILABLE、Tavily AVAILABLE
- Secret 检查：SECRET_LEAK=false（api/worker 日志 0 匹配）
- **Golden Path A（指定来源静态）**：上海市政府官网真实链 **PASSED**（见 §4）
- **Golden Path B（探索式 Search）**：Tavily 真实探索链 **PASSED**（见 §6）

## 3. 本轮确认的 4 个根因 + 修复（均带针对性回归测试）
1. **URLResource 状态持久化（原 Gate-3 blocker）** — `fix(discovery): scope frontier state updates to task resource`（6000f44）
   - 根因：`UrlFrontierRepository._owned(user_id, url_hash)` 缺少 task_id 过滤；URLResource 唯一身份是 (task_id, url_hash)，跨 Task 同 URL 时 `.first()` 选中最早 Task 的行 → mark_state 改写错误行 → 当前 Task 永远 DISCOVERED。
   - 修复：`_owned` 与 mark_state/mark_blocked/mark_fetch_outcome/increment_discovery_count 全部增加 task_id 并按任务作用域过滤；31 处调用点传入所属 task_id。新增 Task A/B/C 同 URL 隔离回归测试。
2. **HTTP 请求 WAF 403** — `fix(crawl): send canonical user-agent on fetch and discovery requests`（bb286a0）
   - 根因：discovery/fetch transport 发送 python-httpx 默认 UA；shanghai.gov.cn WAF 对其 403（KairosBot/浏览器 UA 200）。`DEFAULT_USER_AGENT` 仅用于 robots 策略匹配，从未应用到实际 HTTP 请求。
   - 修复：`DEFAULT_USER_AGENT` 上移到 http.py，两个 httpx transport 默认携带 KairosBot（显式 headers 保留）。新增 transport UA 回归测试。
3. **Extraction LLM fallback 模型解析为 placeholder** — `fix(extraction): resolve frozen model config from plan column with user object`（cea7263）
   - 根因：`ExtractionModelResolver.resolve_for_run` 读 `plan.payload["model_config_id"]`（恒 None，persist_plan 存的是列），且以 `run.user_id` int 调 ProviderService（方法内部 `user.id` → AttributeError 被吞）→ 返回 None → 提取落到 placeholder provider → 全部 snapshot 提取失败。
   - 修复：读 `plan.model_config_id/version` 列；以 User 对象调用。新增 2 个回归测试。
4. **URL 类型字段被文本 grounding 拦截** — `fix(extraction): skip body-text grounding for url-type fields`（7822ca8）
   - 根因：`原文URL` 等 URL 字段的值是页面自身 URL，不在正文中，`evidence_is_grounded` 必然 False → 含必填 URL 字段的 record 全部 needs_review。
   - 修复：pipeline LLM fallback 仅对非 URL 字段强制 grounding（URL 仍过 normalize_url + schema 校验）。新增回归测试。

## 4. Golden Path A（指定来源静态）执行结果 — PASSED
Target: `https://www.shanghai.gov.cn/zzbzfwj/`（上海市人民政府 · 政府文件）
Date Range: 2026-06-11 ~ 2026-08-11
Task 39（gate3.a@kairos.test, run 28, workflow task-workflow-39）：
- Discovered URLs: **24**（seed + 导航页 + 22 个文件详情页，均在范围内）
- Fetched: **24 / 24**（HTTP 200，真实 PageSnapshot 落库）
- Snapshots: **24**
- Records: **23**（passed **18** / needs_review **5** / rejected 0）
- Evidence spot check: **PASS**（record 22/23 全部字段含 FieldEvidence: extract_method=llm, extractor_version=m11.1, snapshot_id, source_url, raw_snippet）
- CompletionDecision: `PARTIALLY_COMPLETED`，qualified=18，completion_type=access_limited（5 条 needs_review 为导航/列表页，属真实数据情况）
- 采样记录：record 22「上海市人民政府关于修改部分市政府规章的决定」发布日期 2026-08-10 / 文号 沪府令〔2026〕26号；record 23「上海市人民政府关于公布微型轻型小型无人驾驶航空器适飞空域范围的通告」发布日期 2026-08-07 / 文号 沪府发〔2026〕12号

完整真实链已跑通：User Task → CollectionSpec → Plan(DeepSeek) → AccessRulesCheck → robots → LinkDiscovery → URL Frontier → HTTP Fetch → PageSnapshot → Extract → FieldEvidence → Deduplicate → Validate → PASSED/NEEDS_REVIEW → CompletionDecision。

## 5. 遗留观察（非本轮 blocker，不阻塞 PASS）
- Golden A 的 5 条 needs_review 来自站点导航/列表页（无单文件发布日期/文号），属真实数据 → 正确进入 Review。
- Task 38 出现过一次 Plan `PROHIBITED`（LLM 生成 `bypass_captcha:true` 被 Validator 正确拦截并拒绝启动），为 LLM 生成偶发，重跑即 VALID；系统按设计未启动被禁止计划。
- Golden B 中 stcn.com 站点被 WAF 识别为 `CAPTCHA_REQUIRED`（真实反爬），系统按设计记录 FETCH_FAILED 并继续处理其他来源；2 个 sheitc.sh.gov.cn 动态页面产生 `BROWSER_PENDING` escalation evidence（属真实动态能力缺口的第一手证据，见 §7/§8）。

## 6. Golden Path B（探索式 Search）执行结果 — PASSED
Target: 纯探索式，无 seed URL（SearchProvider 仅凭自然语言目标发现候选站点）
Goal: 「搜索上海最近两个月公开发布的人工智能或智能制造相关政策，获取标题、发布日期、发文机关、文号和原文链接。」
Task 44（gate3.a@kairos.test, run 30, workflow task-workflow-44）：
- 查询：`上海 人工智能 智能制造 政策 最近两个月`（provider: **tavily**）
- 事件链：task.plan_generated(VALID) → `discovery.candidates_found{candidate_sites:5, provider:tavily}` → `access_checked{checked:5, blocked:0}` → `discovery.expanded{seeds:5, added:78, blocked:0}`
- URLResources：SEARCH_RESULT ×5（4 FETCHED；stcn.com `CAPTCHA_REQUIRED`）+ INTERNAL_LINK 扩展 70+（来源：sh-hitech.com / shyp.gov.cn / m.shopex.cn / sheitc.sh.gov.cn）
- Snapshots: **50 stored**（真实 PageSnapshot 落库）
- Records: **38**（passed **18** / needs_review **20**，needs_review 均为 `missing_required`——真实页面缺必填字段）
- Evidence: **118** 条 FieldEvidence（extract_method=llm, extractor_version=m11.1, snapshot_id/source_url 齐全）
- CompletionDecision: `PARTIALLY_COMPLETED`，qualified=18，completion_type=access_limited
- 采样记录：record 121「2026年上海市制造业智能化发展项目申报通知」（https://www.sh-hitech.com/kjcx/21463.html，2026-01-13）
- 完整真实链：User Task → CollectionSpec → Plan(DeepSeek, VALID) → AccessRulesCheck → Tavily Search → LinkDiscovery 扩展 → URL Frontier → HTTP Fetch → PageSnapshot → Extract → FieldEvidence → Deduplicate → Validate(38/38) → CompletionDecision。

## 7. Golden Path C（动态 Playwright E2E）— DEFERRED_FAST_DEV
- 状态：**DEFERRED_FAST_DEV**（不是 PASS / FAILED / NOT_TESTED）
- 原因：`DYNAMIC_PLAN_GENERATION_UNSTABLE`
- 三次真实 LLM Plan Generation（Task 45/46/47）产生三种不完整 Dynamic Pipeline（见 §8）
- 本轮明确不修复：Dynamic Page Plan Generator、fetch + browser_render 组合、render_if_empty、Validator 对动态 Plan 的强制约束、BrowserRender Plan 生成策略
- 保留全部诊断证据：Task 45/46/47 + Run + PlanVersion + DomainEvent + 诊断 Evidence（不删除，作为技术债证据）

## 8. 技术债：DEFERRED-DYNAMIC-E2E-01

**问题**：LLM Plan Generator 对动态网页不能稳定产生完整 `Fetch → escalation evidence → BrowserRender` pipeline。

**已知形态**（三次真实 LLM Plan Generation）：
1. Task 45：`browser_render` without `fetch`
2. Task 46：`fetch(render_if_empty)` without `browser_render`
3. Task 47：plain `fetch` without `render_if_empty` and without `browser_render`

**风险**：真实 Dynamic Task 可能无法进入 Playwright。

**当前确认能力**：HTTP 动态 shell 的 `BROWSER_PENDING` escalation evidence 已能产生；Playwright 未进入完整业务链的原因是该 Plan 不完整（contract/design-level），而非 fetch/render 实现单点 Bug。

**延期处理阶段**：优先放到 `DEPLOY-GATE-4` 或动态执行契约专门修复轮；不创建新模块；本轮不修。

## 9. 代码变更分类（收口时）
- **本轮确认修复（KEEP，均带回归测试）**：`6000f44`（frontier task_id scope）、`bb286a0`（UA）、`cea7263`（extraction model resolver）、`7822ca8`+`66b82e1`（URL grounding + lint）
- **此前确认修复（KEEP）**：`9a77402`（tavily adapter）、`37aec80`（activity 注册）、`e83a652`（seed 摄入）
- **Golden C 实验代码（已回退）**：`6299223` 回退 5 个仅用于 Golden C 的 commit（`903b9d8`、`6f53936`、`c5a27d7`、`5d9d17d`、`4306c65`），代码恢复到 `c9c88e4`（Golden A PASS）确认状态；回退后 plan/discovery/crawling scoped 测试 23 项通过、ruff lint 通过
- 诊断 instrument（已 revert）：`f177cc5`、`d243312`、`f0edd65`、`4099b26`（e68b6be revert）
- 无 push / merge / tag（均在本地 feature/M-12-validation-quality 分支）

## 10. 结论
**DEPLOY-GATE-3 = PASS_FAST_DEV**（FAST DEVELOPMENT WAIVER，用户 2026-08-12 批准）。Golden Path A 与 Golden Path B 真实链均 PASSED；Golden Path C（动态 Playwright E2E）因 Dynamic Plan Generation 不稳定延期为 `DEFERRED_FAST_DEV`（技术债 `DEFERRED-DYNAMIC-E2E-01`），不再阻塞 M-13。**M-12 = DEPLOYED，M-13 = UNBLOCKED**。下一模块 M-13（Data / Review / Record Drawer / Manual Review / Batch Review / Realtime Records）基于 Golden A / Golden B 已产生的真实 Records 继续。
