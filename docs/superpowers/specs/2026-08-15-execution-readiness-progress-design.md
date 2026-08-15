# Execution Readiness and Observable Progress Design

> 日期：2026-08-15
>
> 状态：设计方向已确认，待书面评审
>
> 事故基线：Production Task 25，release `v0.1.6` / Git `adc594786a11`
>
> 关联模块：M-06 / M-07 / M-08 / M-09 / M-10 / M-11 / M-12 / M-14 / M-15 / M-16 / M-17 / M-18

## 1. 背景

Task 25 的 Production 证据确认：Workflow 已进入 Temporal 并执行 7 个 Plan 单元，但初始 `URLResource` 为零；前 6 个 executor 在空输入上返回 `OK + 0`，`generate_artifact` 返回 `NODE_EXECUTOR_UNAVAILABLE`，最终被错误归类为 `PARTIALLY_COMPLETED/access_limited`。Task Chat 未消费已有 SSE，用户只能看到 `running → partially completed` 和 `0/0`。

事故报告：`docs/audits/task-25-execution-incident.md`。

本设计把“Plan 结构有效”和“可安全启动执行”拆成两个明确门禁，并以现有 `DomainEvent + SSE` 建立可回放、owner-safe 的执行进度。Temporal 保持为唯一 durable orchestration engine。

## 2. 设计目标

本轮必须实现：

- 命名来源、显式 URL 与搜索能力之间的确定性 source contract；
- `ExecutionPreflight`：在 Plan VALID 与 Workflow start 之间验证初始资源可物化和 node executor 完整性；
- `generate_artifact` 的真实、幂等 Production executor；
- 零资源、零候选、零匹配、真实部分完成和运行时失败的互斥终态语义；
- Run/Node lifecycle 的持久化事实与 owner-scoped SSE 映射；
- Task Chat 中可刷新、可重连、不会重复的历史 + 实时执行时间线；
- 全链路 typed error、结构化安全日志和 TDD 验收；
- 真实 Staging 后才能 immutable GHCR 发布 Production，并进行浏览器验收。

## 3. 非目标

本轮明确不做：

- 不替换、绕过或缩减 Temporal；
- 不引入 Kafka、Redis pub/sub、WebSocket 或第二张 `execution_events` 表；
- 不直接把 Temporal history 暴露给浏览器；
- 不展示模型 chain-of-thought、隐藏 reasoning、credential、authorization header 或完整页面正文；
- 不把 named source 自动扩展为无限制全网搜索；
- 不把所有 `OK + 0` 都改成失败；无输入可合法发生在后继分支，但首个执行输入必须通过 preflight；
- 不新增全局 TaskState；优先复用现有 `FAILED`、`COMPLETED`、`PARTIALLY_COMPLETED`，通过 typed reason/completion type 表达细分结果；
- 不在服务器上修改源码或构建镜像；
- 不顺带修复 Plan payload 中非因果性的 stale `task_id`，除非实施时证明它进入本次新边界。

## 4. 核心不变量

### 4.1 Source Contract

1. `SPECIFIED_SOURCE` 必须至少有一个规范化、允许协议的 literal URL。
2. 用户只给出命名网站、机构或平台而没有 literal URL 时，不得保留为空 seed 的 `SPECIFIED_SOURCE`。
3. named source 且 owner 有可用 search configuration 时，Goal Understanding/Spec normalization 确定性转为 `HYBRID`：
   - 保留 `source_hints`；
   - search 只用于解析该命名官方来源及其入口；
   - 不扩大用户指定的机构、地域、主题与时间边界。
4. named source 且没有 search capability 时，返回 `SOURCE_RESOLUTION_REQUIRED` 并要求用户补充 URL；不得确认 Spec 后自动启动。
5. literal URL 存在时保持 `SPECIFIED_SOURCE`，规范化去重后写入非空 `seed_urls`。

### 4.2 Plan 与 Execution Readiness

1. `PlanValidationResult.VALID` 只证明 schema、DAG、resource compatibility、risk 与 Spec boundary 合法。
2. `ExecutionPreflightResult.READY` 才证明本次冻结 Plan 可以在当前 Production capability 和输入上启动。
3. 自动启动必须同时满足：Spec confirmed、Plan VALID、Preflight READY。
4. `HYBRID` / `EXPLORATORY` Plan 必须包含位于资源消费节点之前的 `source_search`。
5. `SPECIFIED_SOURCE` Plan 必须能从冻结 Spec 中物化至少一个 seed resource。
6. Plan 中每个 node type 都必须在相应 Production worker role/task queue 上有明确 executor support；不能因 fixture/test registry 存在而通过。
7. 参数中出现 `url_template`、source hint 或示例 URL，不等于已经物化 `URLResource`。

### 4.3 Completion Semantics

以下结果互斥：

| 场景 | Task final state | typed reason / completion type | 是否 partial |
| --- | --- | --- | --- |
| 无可物化初始来源 | 不启动；保持可修订状态 | `SOURCE_RESOLUTION_REQUIRED` | 否 |
| Plan node 无 Production executor | 不启动 | `EXECUTION_CAPABILITY_UNAVAILABLE` | 否 |
| 运行中 executor 意外不可用 | `FAILED` | `NODE_EXECUTOR_UNAVAILABLE` | 否 |
| source search 成功但没有候选入口 | `COMPLETED` | `NO_MATCHING_PAGES` | 否 |
| 已抓取合格页面，但没有符合字段/规则的记录 | `COMPLETED` | `NO_MATCHING_RECORDS` | 否 |
| 用户停止、访问限制或预算限制导致只处理了真实非空子集 | `PARTIALLY_COMPLETED` | 现有明确 partial reason | 是 |
| 全部 eligible scope 到达 terminal 且无失败 | `COMPLETED` | `SUCCESS` 或上述 empty-success reason | 否 |
| 不可恢复执行错误 | `FAILED` | 对应 stable error code | 否 |

`PARTIALLY_COMPLETED` 的必要条件：存在真实 eligible work，且 `0 < terminal_work < eligible_work`，或存在可审计的用户停止/预算/访问限制并已完成非空工作。`eligible=0 && terminal=0` 永远不能单独推出 partial。

## 5. Source Resolution 设计

### 5.1 输入分类

在 Goal Understanding 产出之后、SpecDraft 返回 UI 之前运行 deterministic source normalization：

```text
literal_urls = normalize_and_validate_urls(seed_urls + URLs extracted from explicit user source text)
named_hints = normalize_hints(source_hints)

if literal_urls:
    mode = SPECIFIED_SOURCE
    seed_urls = literal_urls
elif named_hints and search_capability_available(owner):
    mode = HYBRID
    seed_urls = []
    resolution_scope = NAMED_SOURCE_ONLY
elif named_hints:
    block confirmation with SOURCE_RESOLUTION_REQUIRED
else:
    follow existing exploratory clarification rules
```

URL extraction只处理用户明确提供的 `http://` / `https://` literal，不根据机构名猜域名。协议、host、去重和长度限制沿用当前 URL security policy；不得接受 credential-in-URL、非 HTTP(S) scheme 或内网/保留地址绕过。

### 5.2 Search Scope

`HYBRID + NAMED_SOURCE_ONLY` 的 `source_search` 输入至少冻结：

- 用户的 named source hints；
- 主题与字段约束；
- 行政地域与时间范围；
- 只接受能够证明与命名来源相符的候选 host/page；
- search provider config id/version reference，不保存 credential。

候选进入 frontier 前继续执行现有 URL security、robots/access policy 和 scope checks。Search failure 与 no candidates 必须区分：前者 typed failure/retry，后者是可解释 empty result。

## 6. ExecutionPreflight

### 6.1 位置与职责

固定调用链：

```text
confirm Spec
  → generate and validate Plan
  → persist frozen PlanVersion
  → ExecutionPreflight
  → READY: idempotent Workflow start
  → BLOCKED: do not start; persist typed fact; return actionable response
```

Preflight 是同步、无外部抓取副作用的 deterministic service。它不调用搜索、HTTP fetch、browser 或 LLM；只读取冻结 Spec/Plan、owner capability references、node registry manifest 和队列路由配置。

### 6.2 检查项目

`ExecutionPreflight` 必须检查：

1. Source mode 与 materializable first input：
   - specified → 至少一个合规 seed URL；
   - hybrid/exploratory → 有 `source_search` 且 search configuration 可解析、版本存在、状态可执行。
2. Plan 起始节点至少有一个能够产生或消费首个持久化 resource 的合法路径。
3. 所有 Plan node type 在 Production executor capability manifest 中有实现。
4. node 的 `ResourceClass` 有确定的 Task Queue 路由。
5. 所需 frozen model/search config version 存在且属于 owner；不读取 secret 明文。
6. artifact node 所需 object storage/export capability 已配置。
7. Plan version、Spec version、Task id 和 owner reference 一致。

### 6.3 输出

```text
ExecutionPreflightResult
  status: READY | BLOCKED
  checked_plan_version: int
  checked_spec_version: int
  capability_manifest_version: string
  issues: [
    {code, node_id?, field?, safe_message, remediation}
  ]
```

稳定 issue code 至少包括：

- `SOURCE_RESOLUTION_REQUIRED`
- `SEARCH_CONFIGURATION_REQUIRED`
- `EXECUTION_INPUT_UNMATERIALIZABLE`
- `EXECUTION_CAPABILITY_UNAVAILABLE`
- `TASK_QUEUE_ROUTE_UNAVAILABLE`
- `FROZEN_CONFIG_UNAVAILABLE`
- `ARTIFACT_STORAGE_UNAVAILABLE`
- `PLAN_CONTEXT_MISMATCH`

Preflight result 必须写入 owner-scoped domain fact/event，便于 UI 解释为何没有启动。Issue payload 只含安全标识、node id、field 和中文 remediation，不含 credential、prompt 或 URL query secret。

### 6.4 幂等与竞态

- Preflight 以 `task_id + plan_version + spec_version + capability_manifest_version` 为事实键。
- 同一冻结输入重复调用返回同一语义结果。
- `auto_start` 必须再次验证将启动的版本与 READY result 一致；版本变化则拒绝并要求重新 preflight。
- Workflow ID 继续沿用稳定的 `task-workflow-{task_id}`，Run 创建与 start failure 遵循现有幂等边界。

## 7. Executor Capability 与 `generate_artifact`

### 7.1 Registry Contract

当前动态 `NODE_EXECUTORS` 继续用于 Activity dispatch，但新增一个不依赖运行时副作用的 capability manifest，使 Plan validation/preflight 能查询：

```text
node_type → implementation_id → resource_class → task_queue → enabled_roles
```

测试 fixture executor 必须标记为非 Production capability，不能进入 Production manifest。

启动时应校验 worker role 声明与 capability manifest 一致，并记录安全日志；preflight 读取同一权威定义，避免“Validator 知道 node、Worker 不知道 executor”。

### 7.2 Artifact Executor

为 `NodeType.GENERATE_ARTIFACT` 注册真实 executor，内部复用现有 `ArtifactService`，不得复制 CSV 规则或返回 fixed success。

输入至少包括 owner、task、run、plan version 与 artifact format；这些值从冻结 execution context 获取，模型参数不能覆盖 owner/run binding。

行为：

1. 从 owner-scoped、当前 Run 的已验证 Records 生成 artifact。
2. 使用现有 object storage boundary 写入对象。
3. 复用 ArtifactService 的 content-aware/idempotent semantics；同一输入重试不得生成冲突副作用。
4. 持久化 Artifact reference 和计数，不把文件内容写入 DomainEvent。
5. typed failure 保留 retryability；storage/transient error 可按现有 Activity policy 有界重试，validation/ownership/config error 不重试。
6. 即使 `NO_MATCHING_RECORDS`，是否生成 headers-only artifact 必须沿用现有 ArtifactService 产品契约；无论生成与否，不能把空结果伪装为 partial。

## 8. Run 与 Node 执行事实

### 8.1 持久化模型

复用现有 `Run`、`NodeRun`、`NodeAttempt`、`Checkpoint` 与 `DomainEvent`：

- `Run`：Workflow 一次业务执行及最终 outcome；
- `NodeRun`：冻结 Plan node 在该 Run 中的聚合状态；
- `NodeAttempt`：每次 Activity attempt 的开始、结束、错误分类和计数；
- `Checkpoint`：可恢复的业务处理游标/结果引用；
- `DomainEvent`：供 API/UI 消费的安全、有序投影事实。

不新增第二个 event store。若现有模型字段不足，使用最小 migration 扩充稳定状态/计数/时间戳，不保存 reasoning 或大 payload。

### 8.2 生命周期

稳定 lifecycle：

```text
RUN_STARTED
NODE_STARTED
NODE_PROGRESS (optional, bounded/coalesced)
CHECKPOINT_COMMITTED
NODE_COMPLETED | NODE_BLOCKED | NODE_FAILED
RUN_COMPLETED | RUN_PARTIALLY_COMPLETED | RUN_FAILED | RUN_CANCELLED
```

事实写入由 Activity/application service 完成，不在 Temporal Workflow deterministic code 内直接访问数据库。每个写入 command 使用稳定幂等键，例如：

```text
run:{run_id}:node:{node_id}:attempt:{attempt}:started
run:{run_id}:node:{node_id}:attempt:{attempt}:completed
run:{run_id}:checkpoint:{checkpoint_id}
run:{run_id}:terminal:{state}
```

Activity retry、Worker restart 或 Workflow replay 不得产生语义重复事件。Event id 仍由数据库形成单调 owner-scoped 顺序。

### 8.3 事件最小 Schema

所有 UI 可见 execution event payload 使用版本化安全 schema：

```json
{
  "schema_version": 1,
  "task_id": 25,
  "run_id": 8,
  "plan_version": 1,
  "node_id": "n3",
  "node_type": "fetch",
  "attempt": 1,
  "state": "COMPLETED",
  "occurred_at": "...",
  "counts": {
    "discovered": 0,
    "fetched": 0,
    "extracted": 0,
    "validated": 0
  },
  "reason_code": null,
  "safe_message": "抓取完成，共处理 0 个页面"
}
```

字段按事件类型裁剪。禁止包含：credential、authorization header、cookie、完整 provider request/response、模型 reasoning、页面正文、任意未经脱敏的 exception、带 secret query 的 URL。

### 8.4 Event 产生位置

- `ensure_run_started`：原子创建/激活 Run、物化 seeds，并记录 `RUN_STARTED`。
- execution dispatch 前的 recorder Activity/application command：创建或恢复 `NodeRun` / `NodeAttempt`，记录 `NODE_STARTED`。
- executor 完成后：在同一业务事务中保存结果计数/引用、checkpoint、attempt terminal 与相应 events。
- retryable Activity failure：attempt 记失败，但 NodeRun 可保持 retrying；只向 UI 输出 safe retry state。
- 最终失败或 block：保存 typed reason，不能合并成 `approval_rejected_or_executor_unavailable`。
- completion Activity：依据持久化事实计算 outcome，并记录唯一 `RUN_*` terminal event。

对 fetch/extract 等可能高频 executor，`NODE_PROGRESS` 使用计数阈值或最小时隔合并；checkpoint 和 terminal event 不丢弃。

## 9. SSE 与 API

### 9.1 复用边界

继续使用现有 Task SSE endpoint、owner check、`Last-Event-ID` / `after_id` 和数据库回放。后端把 canonical DomainEvent 映射为前端稳定事件名，不读取 Temporal history 作为在线 UI 数据源。

需要扩充的事件类型：

- `RUN_STARTED`
- `NODE_STARTED`
- `NODE_PROGRESS`
- `CHECKPOINT_COMMITTED`
- `NODE_COMPLETED`
- `NODE_BLOCKED`
- `NODE_FAILED`
- `RUN_COMPLETED`
- `RUN_PARTIALLY_COMPLETED`
- `RUN_FAILED`
- `RUN_CANCELLED`
- `EXECUTION_PREFLIGHT_BLOCKED`

### 9.2 Snapshot + Delta

Task Chat 打开时执行：

1. owner-scoped GET 获取当前 execution snapshot 与最近有界 timeline；响应含 `last_event_id`。
2. 以该 event id 打开 SSE delta。
3. event 按 `event_id` 去重并单调合并。
4. 断线后 EventSource 携带 `Last-Event-ID`，后端回放缺失事件。
5. reconnect open 后做一次轻量 snapshot reconcile，覆盖浏览器长时间休眠、服务重启或 retention 边界。

GET snapshot 必须由持久化 `Run/NodeRun/NodeAttempt/Checkpoint/DomainEvent` 投影，不能由前端猜测阶段，也不能返回 fake progress percentage。

### 9.3 Ownership 与 Retention

- snapshot、events 和 SSE 必须复用 Task owner authorization；不存在/非 owner 均保持现有防枚举语义。
- event 查询按 `task_id + owner_id + event_id` 限界。
- timeline 默认有界返回最近事件；replay 上限触发时返回明确 reconcile signal，由前端重新拉 snapshot。
- 不在 URL query 中传 access token 或 credential。

## 10. Task Chat UI

在现有 Plan summary 下增加“执行进度”区域，不新增独立入口作为本轮必要条件。

### 10.1 展示内容

- 当前运行状态与安全的 typed outcome；
- 当前节点中文标签；
- 最近成功节点；
- 上次活动时间；
- 已发现页面、已抓取页面、已抽取记录、已验证记录；
- waiting/blocked/failed 的中文原因与可操作建议；
- 按时间排序的最近节点事件。

节点标签示例：

| node type | 中文标签 |
| --- | --- |
| `source_search` | 解析指定来源 |
| `access_rules_check` | 检查访问规则 |
| `link_discovery` | 发现页面链接 |
| `fetch` | 抓取页面 |
| `browser_render` | 渲染动态页面 |
| `extract` | 提取字段 |
| `normalize` | 规范化数据 |
| `deduplicate` | 去重与冲突检查 |
| `validate` | 验证记录 |
| `generate_artifact` | 生成结果文件 |

### 10.2 状态原则

- 不显示虚构百分比；只显示来自持久化事实的节点状态和计数。
- 运行中无新事件时显示“上次活动于 …”，前端不自行宣布失败。
- 真正 stalled/failure 由 Temporal timeout、heartbeat 和后端 reliability policy 决定，再通过 typed event 呈现。
- `NODE_EXECUTOR_UNAVAILABLE` 显示为系统能力错误，不显示为“等待审批”。
- `NO_MATCHING_PAGES` 和 `NO_MATCHING_RECORDS` 显示为已完成的空结果，并解释检查过的范围。
- 刷新后先渲染 snapshot，再接收增量；不得清空已经完成的节点。

## 11. Error 与 Retry

- Preflight issue 不进入 Temporal retry；用户修订 Spec/配置或系统 capability 变更后重新运行 preflight。
- executor transient failure 沿用各 ResourceClass 的有界 Activity retry。
- NodeAttempt 每次 attempt 都记录 typed error class、retryable 与 safe message；原始 exception 仅进入受控结构化日志且必须脱敏。
- `NODE_EXECUTOR_UNAVAILABLE` 理论上由 preflight 消除；若部署漂移导致运行中出现，立即 `FAILED`，不得 block-and-continue。
- cancellation、approval rejected、access denied、budget exhausted 和 executor unavailable 使用不同 reason code。
- completion 只读取持久化 scope/work facts，不根据模糊文案推断状态。

## 12. 兼容与迁移

- 历史 Run 没有 NodeRun/NodeAttempt 时，snapshot 返回 `legacy_execution_facts=true`，UI 可显示已有 DomainEvent，不反向伪造节点事实。
- 不回写 Task 25 的历史终态；事故报告保留其原始 Production 事实。
- 新事件 schema 版本从 1 开始；前端忽略未知可选字段，对未知 event type 保留安全 fallback。
- 如需数据库 migration，必须向前兼容当前 API，并进入唯一 Alembic head。
- rollout 后新 Plan 必须通过 preflight；已启动旧 Workflow 保留兼容分支，不能因 deploy 被重复启动。

## 13. TDD 计划边界

实施阶段严格先写失败测试，再写最小实现。最低测试矩阵：

### 13.1 Source Contract

1. explicit URL → `SPECIFIED_SOURCE` + non-empty normalized seeds。
2. named source + available search → `HYBRID` + named-source-only search scope。
3. named source + no search → `SOURCE_RESOLUTION_REQUIRED`，不能确认后自动启动。
4. unsafe/non-http URL 不得成为 seed。

### 13.2 Plan 与 Preflight

1. hybrid plan 缺 `source_search` → invalid。
2. specified plan 空 seeds → preflight blocked。
3. Plan node 无 Production executor → preflight blocked。
4. fixture executor 不得满足 Production capability。
5. missing task queue route / frozen config / artifact storage → 对应 stable issue。
6. READY result 与 start 的 version mismatch → 不启动。
7. 重复 preflight/start → 单一 Run/Workflow 语义。

### 13.3 Executor 与 Completion

1. `generate_artifact` registry contract。
2. Artifact executor owner binding、真实 object storage、重复调用幂等。
3. runtime executor unavailable → `FAILED/NODE_EXECUTOR_UNAVAILABLE`。
4. search no candidates → `COMPLETED/NO_MATCHING_PAGES`。
5. pages processed but no records → `COMPLETED/NO_MATCHING_RECORDS`。
6. eligible=0/terminal=0 不得 partial。
7. 真实非空子集因访问/预算停止 → partial。

### 13.4 Lifecycle 与 SSE

1. node started → checkpoint → node completed → run terminal 的 event ordering。
2. Activity retry 不产生重复 semantic event。
3. NodeRun/NodeAttempt 与 DomainEvent 状态一致。
4. owner 可以读，非 owner 不可读/订阅。
5. `Last-Event-ID` 与 `after_id` 精确回放，不漏不重。
6. reconnect/reconcile 能恢复中断窗口。
7. payload secret scan：credential/header/cookie/reasoning/page body 不出现。

### 13.5 Frontend

1. Task Chat 首屏渲染历史 snapshot。
2. SSE 增量按 event id 去重、排序。
3. 刷新后保留已完成节点并继续接收事件。
4. reconnect 后 reconcile snapshot。
5. running、waiting、blocked、failed、partial、empty success 中文语义正确。
6. `NODE_EXECUTOR_UNAVAILABLE` 不渲染为 high-risk approval。
7. 无 fake percentage，无 chain-of-thought。

### 13.6 Regression

- 当前 Spec/Plan/approval/cancellation/ownership tests；
- Temporal replay/idempotency tests；
- discovery/fetch/extraction/validation/artifact tests；
- frontend TaskStatusDrawer 与 record event consumers；
- migration single-head、lint、typecheck、build 和 full CI。

## 14. Staging 验收

至少执行下列真实任务，不使用 fixture provider 或 fake executor：

1. Task 25 同等输入：只写“山东省人民政府官网”，不提供 URL；在 search 可用时应生成受限 `source_search` 并显示实时节点进度。
2. explicit official URL：应直接物化 seed，不调用无必要的全网 source resolution。
3. named source + 临时禁用该测试用户 search config：必须在 Workflow start 前返回 `SOURCE_RESOLUTION_REQUIRED`。
4. 可正常访问但无匹配记录的窄时间窗：最终为 `COMPLETED/NO_MATCHING_RECORDS`，不是 partial。

每个任务验收：

- 浏览器能在 Task Chat 看到节点开始、完成、计数和上次活动；
- 刷新与断网重连后时间线一致；
- Temporal history、数据库 Run/NodeRun/NodeAttempt/Checkpoint/DomainEvent 和 UI 一致；
- artifact 可下载且 owner-safe；
- 日志与 Temporal history secret scan 通过；
- 无 `NODE_EXECUTOR_UNAVAILABLE`、无 `0/0 → PARTIALLY_COMPLETED`。

## 15. Production 发布与验证

发布必须遵循仓库 Git 和 Production Deployment Standard：

```text
branch
  → PR
  → required CI green
  → merge to main
  → immutable GHCR images by digest
  → Staging deploy + real acceptance
  → Production backup + manifest
  → Production deploy
  → health + migration + browser + Temporal/DB consistency verification
```

Production 首个验证任务使用 owner 自己的测试 Task，不复用或修改 Task 25。验收必须保存 release tag、Git SHA、image digests、migration head、workflow/run identity、浏览器截图或结构化记录，以及无敏感信息的事件序列。

回滚只使用上一个已验证 immutable image digest 和对应 manifest；数据库变化必须满足向后兼容。回滚后验证旧版本健康、现有 Workflow 不被重复启动、owner 数据无丢失。

## 16. 可观测性与告警

新增低基数 metrics：

- preflight READY/BLOCKED 按 stable issue code；
- Workflow start 与 Run terminal 按 outcome；
- node attempts/completed/failed 按 node type、resource class、reason code；
- Run 最后活动时间与 active-without-event duration；
- SSE active connections、replay count、reconcile count；
- `eligible=0 && partial` invariant violation，目标永远为 0；
- Plan admitted but executor unsupported，目标永远为 0。

日志 correlation 至少包含 task id、run id、plan version、node id、attempt、workflow id 和 safe reason code。禁止用用户 title、prompt、credential 或完整 URL 作为 metric label。

## 17. 方案取舍

### 采用：DomainEvent + SSE + ExecutionPreflight

优势：复用现有 owner/replay/order 基础；UI 不耦合 Temporal；数据库事实可审计；新增边界集中在 source contract、preflight、executor completeness、completion 和事件投影。

### 不采用：新增 execution_events 表

它会与 DomainEvent 形成双 event store，需要额外 migration、事务一致性、回放和 retention 机制，无法解决 source/preflight/completion 根因。

### 不采用：浏览器直接读取 Temporal history

它会让 API/UI 耦合 Temporal 内部事件格式，增加 authorization、payload 脱敏、history pagination 和版本兼容成本；Temporal history 继续用于 orchestration 审计，产品 UI 使用领域事实。

## 18. 完成定义

只有同时满足以下条件才能宣称修复完成：

- 设计与实施计划经评审；
- TDD tests、全量回归、lint/typecheck/build、migration single-head 全绿；
- `generate_artifact` 为真实 executor，registry/preflight 一致；
- 四个 Staging 场景通过且有 Temporal/DB/UI 一致性证据；
- PR required checks 通过并合并；
- immutable GHCR digest 发布，Production manifest/backup/rollback target 完整；
- Production 浏览器真实验证进度、刷新/重连、empty result 和 artifact；
- Production 不再出现 `eligible=0 && PARTIALLY_COMPLETED` 或 admitted unsupported node；
- 没有 secret、chain-of-thought、跨用户事件或 fake progress 泄露。
