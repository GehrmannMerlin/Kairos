# M-09 模块执行记录

状态：**DONE_LOCAL**（2026-08-11）
负责人/Agent：Claude Code
Baseline（DEPLOY-GATE-2 PASS）SHA：`fcba4c6`（docs(deploy): close DEPLOY-GATE-2）
分支：`feature/M-09-source-discovery-frontier`（pushed：NO）
依赖模块：M-03（SearchProvider/SearchConfig/CredentialVault）、M-04（URLResource/Idempotency/Checkpoint）、M-07（Temporal TaskWorkflow）、M-08（Node Registry/Approval）
目标环境：local（M-09 不部署；下一强制 Gate 为 M-09～M-12 后的 DEPLOY-GATE-3）

## 1. 模块目标
完成 D-068～D-070 的两阶段来源发现（外部发现 → 候选站点 → 站内 URL 扩展），
实现探索式/混合式任务真正能找到候选站点并在站内安全扩展 URL，形成可审计、可恢复、
幂等、Checkpointable 的 URL Frontier，供 M-10 以 READY_FOR_FETCH 消费。

## 2. 契约
- `app/discovery/url.py`：`canonical_url` / `url_hash` / `canonicalize_and_hash`（确定性规范化）
- `app/discovery/ssrf.py` + `app/discovery/http.py`：SSRF 守卫 + 最小 `DiscoveryHttp`（get_text/head，每请求+每跳重定向逐次校验）
- `app/discovery/robots.py`：`RobotsPolicy` / `parse_robots` / `fetch_robots` / `RobotsCache`（按 site origin 缓存 TTL，含 Sitemap 指令）
- `app/discovery/models.py`：`DiscoverySource` / `FrontierState` / `DiscoveryEvidence` / `CandidateSite` / `SearchResultRef` / `priority_for`
- `app/discovery/frontier.py`：`UrlFrontierRepository`（task_id+url_hash 唯一约束去重、幂等 upsert、状态迁移、READY_FOR_FETCH 查询）
- `app/discovery/source_search.py`：`SearchService`（SourceSearch executor，复用 M-03 SearchProvider/SearchConfig/CredentialVault）
- `app/discovery/access_rules.py`：`AccessRulesService`（AccessRulesCheck executor + robots override JIT Approval）
- `app/discovery/link_discovery.py`：`LinkDiscoveryService`（sitemap/RSS/Atom/HTML 导航/分页/内链扩展）
- `app/discovery/executors.py`：`install_discovery_executors()`（注册三 executor 进 M-08 NODE_EXECUTORS）
- `app/activities/discovery_approval.py`：`resolve_robots_override` activity（consume 复验 + Frontier 迁移）
- `app/workflows/task_workflow.py`：新增 `WAITING_APPROVAL` 分支（robots override 等待/恢复）
- Migration：`0007_extend_url_resource_frontier.py`

## 3. 行为
- SourceSearch：消费 Plan 已验证参数（query/max_results/locale），不调用 LLM 生成 query；
  缺可用 SearchConfig → 稳定 `SEARCH_PROVIDER_NOT_CONFIGURED`；SPECIFIED_SOURCE 计划不含
  SourceSearch 节点天然可继续。搜索结果合并 Candidate Sites（保留 query/provider/rank/result URL
  证据），写入 Frontier（SEARCH_RESULT）。
- AccessRulesCheck：scheme/host/scope/robots 决策；robots denied 且公共（HEAD 探测非 401/403）
  → 复用 M-08 ApprovalService 创建 JIT Approval → WAITING_APPROVAL；auth/private/access-controlled
  → BLOCKED（不可覆盖）。用户批准后 `resolve_robots_override` consume 复验 fingerprint → READY_FOR_FETCH。
- LinkDiscovery：对 ACCESS_ALLOWED 站点 seed 做 sitemap（robots Sitemap directive + /sitemap.xml fallback）、
  RSS/Atom、HTML 导航/分页/内链扩展；URL 规范化 + robots/scope 决策 → READY_FOR_FETCH（允许）或 BLOCKED（robots 拒绝）；
  跨域链接仅作为候选提示不入 Frontier。
- Frontier：canonical 去重由 DB 唯一约束（task_id+url_hash）兜底；重复发现累加 discovery_count 与证据；
  每批 DB 事务提交即 checkpoint，Worker 重试按幂等 upsert 续跑。
- SSRF：拒绝 localhost/127.0.0.0/8/::1/link-local/169.254.0.0/16/RFC1918/云 metadata/file/ftp；
  字面 IP 与 DNS 解析后 IP 都必须为公网；每跳重定向重新校验；测试用显式 allow_hosts 绕过，Production 默认关闭。

## 4. 明确不做（M-10+）
完整 HTTP Fetch / PageSnapshot / Scrapy / Crawl4AI / Playwright / BrowserRender /
Extract / Normalize / Record Dedup / Quality / CSV / 凭据访问。资源池调度（M-16）。
不新增页面（13 页边界保持）。不部署 Staging。

## 5. 验收证据
### scoped tests
```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/discovery -q
# 38 passed（url/ssrf/robots/frontier/source_search/access_rules/link_discovery/e2e/executor_binding）
# 含 SSE 聚合事件断言（discovery.access_checked / discovery.expanded / candidates_found）
.venv/Scripts/python.exe -m pytest tests/integration/test_m09_discovery_workflow.py -q
# executor 绑定无栈验证 PASS；2 条 Temporal 场景收集跳过（本地栈未启动，与 M-08 先例一致）
```
E2E 三场景：A 指定来源 seed → AccessRules → LinkDiscovery → Frontier READY_FOR_FETCH；
B 探索式 Fake Search → SourceSearch → AccessRules → LinkDiscovery → Frontier；
C robots denied 公共 URL → JIT Approval → approve → resolve → READY_FOR_FETCH。

### ruff / mypy / secret scan
```bash
.venv/Scripts/python.exe -m ruff check app tests          # PASS
.venv/Scripts/python.exe -m mypy app                      # PASS
```
新增代码无 API Key/密码/私钥模式匹配；`sk-fake` 等 dummy 仅存在于测试 fixture。

### Migration
```bash
.venv/Scripts/python.exe -m alembic heads   # 0007 (head)
.venv/Scripts/python.exe -m alembic upgrade head --sql   # 0007 url_resources 扩展列生成
```

## 6. Git 证据（feature/M-09-source-discovery-frontier，基线 fcba4c6，pushed NO）
| Commit | 内容 |
|---|---|
| 8218031 | feat(discovery): add deterministic url canonicalizer and identity |
| 3d5f35f | feat(discovery): add ssrf-guarded discovery http transport |
| 972f468 | feat(discovery): add robots txt fetch parse and policy cache |
| 758c0df | feat(discovery): add persistent url frontier with canonical dedupe（migration 0007）|
| dadd84d | feat(search): execute source search providers with stable semantics |
| 6405a88 | feat(discovery): add access rules check with robots approval boundary |
| 9617d47 | feat(discovery): add sitemap rss atom and html link expansion |
| b6276cf | feat(workflow): connect discovery node executors to temporal |
| （待） | docs(discovery): record M-09 execution |

## 7. 跨模块联动结果
- 上游 M-03 SearchProvider/SearchConfig/CredentialVault：PASS（SourceSearch 复用，无第二套 Search）
- 上游 M-04 URLResource/Idempotency：PASS（复用 URLResource + 唯一约束兜底）
- 上游 M-07 TaskWorkflow：PASS（WAITING_APPROVAL 分支处理 robots override；全部 HTTP 在 executor/Activity）
- 上游 M-08 Node Registry/Approval：PASS（复用注册节点 + ApprovalService）
- 下游 M-10 Handoff：READY_FOR_FETCH URLResource 含 task/spec/run/canonical URL/access/evidence/priority

## 8. 完成结论
**M-09 = DONE_LOCAL**。下一阶段：M-10（不要开始）；DEPLOY-GATE-3 在 M-09～M-12 完成后。
