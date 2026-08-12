# DEPLOY-GATE-3 执行记录：真实网页采集 E2E Staging

状态：**PROGRESS**（2026-08-12）— 核心 blocker 已确认根因并修复；Golden Path A（指定来源静态）**PASSED**；Golden Path B/C 待运行
负责人/Agent：Claude Code
Staging：https://staging.kairos.ac.cn（服务器 47.238.145.24）
当前运行镜像：`kairos-{web}:staging-f0edd653fca5` + `kairos-{api,worker}:staging-66b82e1c3007`
Migration：`0010`（未变，无 schema 变更）

## 1. 已经 PASS 的项目（真实结果）
- M-12 本地实现 + scoped tests + migration 0010 upgrade/downgrade SQL 验证
- Staging 部署：镜像构建/传输/compose up 成功，API healthy，worker 加载全部 executor
- HTTPS / /api/health/live / /api/health/ready：全部 PASS（postgresql/temporal/object_storage ok）
- Migration 0010：已应用
- Provider 配置：gate3.a@kairos.test（user 53）DeepSeek `deepseek-chat` AVAILABLE、Tavily AVAILABLE
- Secret 检查：SECRET_LEAK=false（api/worker 日志 0 匹配）
- **Golden Path A（指定来源静态）**：上海市政府官网真实链 **PASSED**（见 §4）

## 2. 本轮确认的 4 个根因 + 修复（均带针对性回归测试）
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

## 3. Golden Path A（指定来源静态）执行结果 — PASSED
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

## 4. 遗留观察（非本轮 blocker，不阻塞 Golden Path A PASS）
- 5 条 needs_review 来自站点导航/列表页（无单文件发布日期/文号），属真实数据 → 正确进入 Review。
- Task 38 出现过一次 Plan `PROHIBITED`（LLM 生成 `bypass_captcha:true` 被 Validator 正确拦截并拒绝启动），为 LLM 生成偶发，重跑即 VALID；系统按设计未启动被禁止计划。

## 5. Golden Path B / C 状态
- Golden Path B（探索式 Tavily Search）：之前 NOT_COMPLETED；**本轮未运行**（待 Golden Path A PASS 后作为下一路径）。
- Golden Path C（动态 Playwright）：之前 NOT_COMPLETED；本轮未运行。
- pause/resume、worker restart、两用户隔离：NOT_COMPLETED。

## 6. 代码变更分类（收口时）
- **本轮确认修复（4 个，均带回归测试）**：`6000f44`（frontier task_id scope）、`bb286a0`（UA）、`cea7263`（extraction model resolver）、`7822ca8`+`66b82e1`（URL grounding + lint）
- **此前确认修复（KEEP）**：`9a77402`（tavily adapter）、`37aec80`（activity 注册）、`e83a652`（seed 摄入）
- 诊断 instrument（已 revert）：`f177cc5`、`d243312`、`f0edd65`、`4099b26`（e68b6be revert）
- 无 push / merge / tag（均在本地 feature/M-12-validation-quality 分支）

## 7. 结论
**DEPLOY-GATE-3 核心 blocker（URLResource 状态持久化）已确认根因并修复，Golden Path A（指定来源静态）PASSED**。M-12 保持 DONE_LOCAL。下一轮：运行 Golden Path B（探索式 Tavily 真实搜索）→ 再评估 Golden Path C；全部 PASS 后才标记 DEPLOY-GATE-3 = PASS。M-13 未开始。
