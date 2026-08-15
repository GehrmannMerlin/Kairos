# Kairos Task 25 Production Execution Incident

> 取证日期：2026-08-15
>
> 事故范围：Production Task 25 进入 `RUNNING` 后快速变为 `PARTIALLY_COMPLETED`，用户看不到 Agent 执行过程，结果显示 `0/0`
>
> 取证方式：Production 只读查询、Temporal history、容器与 release manifest 核验、仓库静态调用链审计
>
> 安全边界：未读取或记录 credential、authorization header、prompt 私密内容或跨用户业务数据；未对 Production 做写操作

## 1. 结论

Task 25 确实进入了 Temporal Workflow，不是 Workflow 未启动，也不是 Worker/Task Queue 离线。

直接根因是一个未被系统拒绝的“不可执行输入”：用户指定了“山东省人民政府官网”这一命名来源，但没有提供 URL；Goal Understanding 将其归类为 `SPECIFIED_SOURCE`，且生成了空 `seed_urls`。Plan、Validator 和启动前检查均未验证首个可物化输入，Workflow 因而在空 frontier 上真实执行了多个 Activity，并全部返回零处理量。

在最后一步，标准 Plan 中的 `generate_artifact` 没有 Production executor，运行时返回 `NODE_EXECUTOR_UNAVAILABLE`。Workflow 将该事实当成可继续的 blocked node，随后把“零合格 URL、零终止 URL、来源范围未完整处理”归类为 `PARTIALLY_COMPLETED/access_limited`。这个终态掩盖了两个不可恢复错误：初始来源不可解析，以及执行器缺失。

用户不可见的原因是 Task Chat 页面没有消费现有 Task SSE；前端事件类型也没有覆盖 fetch、extract、validation 和通用 node lifecycle。后端虽然已有 `DomainEvent`、SSE、事件回放与所有权校验，但目前没有形成完整、统一的 Run/Node 事件事实。因此 UI 只能显示任务状态和 `0/0`，无法解释 Workflow 实际做过什么。

## 2. Production Release Identity

事故发生时确认的部署身份：

| 项目 | 值 |
| --- | --- |
| release | `v0.1.6` |
| Git SHA | `adc594786a11` |
| migration | `0015` |
| deploy time | `2026-08-15T10:53:38Z` |
| manifest | `/srv/kairos/releases/manifest-v0.1.6.json` |
| web image digest | `sha256:31fd26ba8f107689c988e9b7e1b3cf213c9c5fcdc0acef3264ab07efca8f1430` |
| api image digest | `sha256:c4c460a150817489f176c1113370f1108720ffaf8af7e6c57fe07a40d1277669` |
| worker image digest | `sha256:5ab4c7db1a53c693dc0de432a9d8c4d585711c955361d48c9f5bfe96dae9eb0c` |
| rollback release | `v0.1.5-a3df2245c0a0` |

所有应用容器均在运行，健康检查通过。Worker 启动时注册了 `kairos-task`、`kairos-http`、`kairos-browser`、`kairos-llm-search`；取证时各 Task Queue 均有 poller 且无 backlog。

## 3. Task、Spec、Plan 与 Run 事实

### 3.1 Task 25

| 字段 | 事实 |
| --- | --- |
| task id / owner | `25` / `user_id=4` |
| title | `采集山东省人民政府官网发布的最近一个月的干部任前公示信息` |
| final state | `PARTIALLY_COMPLETED` |
| source mode | `SPECIFIED_SOURCE` |
| created / updated | `2026-08-15 13:52:20Z` / `2026-08-15 13:52:57Z` |
| spec / plan version | `1` / `1` |

Spec 13 的来源约束：

```json
{
  "mode": "SPECIFIED_SOURCE",
  "seed_urls": [],
  "source_hints": [
    "山东省人民政府官网",
    "干部任前公示",
    "公示公告",
    "人事信息"
  ]
}
```

Owner 的 Tavily search configuration 存在且状态可用。系统没有利用它把“命名官网但无 URL”解析为受限搜索，也没有在确认或启动前要求补充 URL。

### 3.2 Plan 8

Plan validation state 为 `VALID`，共 7 个节点：

| 顺序 | node | 参数中的关键事实 |
| ---: | --- | --- |
| 1 | `access_rules_check` | `{}` |
| 2 | `link_discovery` | `{}` |
| 3 | `fetch` | `url_template=https://www.shandong.gov.cn` |
| 4 | `extract` | 字段定义存在 |
| 5 | `normalize` | — |
| 6 | `validate` | — |
| 7 | `generate_artifact` | — |

Plan 没有 `source_search`。`fetch.url_template` 只是模型生成参数，当前执行链不会把它物化成 `URLResource`；运行启动只从 Spec 的 `seed_urls` 写入 frontier。因此这个参数不能弥补空 seed。

Plan payload 内部另有 `task_id=1` 与实际 Task 25 不一致。实际 Workflow 和 Run 均绑定 Task 25，未发现该字段参与本次失败链；它作为独立一致性问题保留，不扩大本事故的修复范围。

### 3.3 Run 8

| 字段 | 事实 |
| --- | --- |
| state | `partially_completed` |
| started | `2026-08-15 13:52:55.534Z` |
| finished | `2026-08-15 13:52:57.769Z` |
| NodeRun / NodeAttempt | `0 / 0` |
| URLResource / PageSnapshot | `0 / 0` |
| Record / FieldEvidence / Artifact | `0 / 0 / 0` |

数据库保存了 6 个 checkpoint：

| checkpoint | 执行结果 |
| ---: | --- |
| 42 | access rules checked `0` |
| 43 | discovery seeds `0`、added `0`、blocked `0` |
| 44 | fetched `0` |
| 45 | extracted `0` |
| 46 | normalized `0` |
| 47 | validated `0` |

`generate_artifact` 没有 checkpoint，因为 executor 不存在。

CompletionDecision 8：

```json
{
  "status": "PARTIALLY_COMPLETED",
  "reason": "指定来源范围未完整处理",
  "is_partial": true,
  "completion_type": "access_limited",
  "qualified_urls": 0,
  "eligible_urls": 0,
  "terminal_urls": 0,
  "scope_complete": false
}
```

这不是“完成了部分来源”的事实，因为从未存在可处理来源。

## 4. Temporal History

| 项目 | 值 |
| --- | --- |
| workflow id | `task-workflow-25` |
| run id | `fc966f70-acaa-4750-9026-9e8bcb66f153` |
| namespace | `kairos-production` |
| primary task queue | `kairos-task` |
| Temporal status | `COMPLETED` |
| workflow result | `{"final_state":"PARTIALLY_COMPLETED","run_id":8,"task_id":25}` |
| runtime | 约 `2.41s` |
| history events | `203` |

Activity 顺序和结果：

1. `ensure_run_started`：成功，Task 转为 RUNNING，Run 已启动；空 `seed_urls` 没有创建 frontier resource。
2. `heartbeat_task_slot`：成功。
3. `access_rules_check`：`OK`，checked `0`。
4. `link_discovery`：`OK`，seeds/added/blocked 均为 `0`。
5. `fetch`：路由到 `kairos-http`，`OK`，fetched `0`。
6. `extract`：`OK`，extracted `0`。
7. `normalize`：`OK`，normalized `0`。
8. `validate`：`OK`，validated `0`。
9. `generate_artifact`：`NODE_EXECUTOR_UNAVAILABLE`。
10. Workflow 调用 `block_high_risk_node`，把执行器缺失表示成 blocked，而不是失败。
11. `resolve_completion` 返回 partial/access_limited/0。
12. `mark_partial` 成功，Workflow 正常完成。

因此：Temporal、Worker、Activity scheduling 与队列路由都真实发生；问题是输入与能力在启动前未被验证，以及错误终态映射不正确。

## 5. DomainEvent 与 UI 事实

Task 25 保存的主要 DomainEvent：

| event id | 事件 |
| ---: | --- |
| 86 | Spec confirmed |
| 87 | Plan generated / `VALID` |
| 88 | task.start / `RUNNING` |
| 89 | discovery.expanded / seeds `0`、added `0`、blocked `0` |
| 90 | validation.started / records `0` |
| 91 | validation.completed / validated `0` |
| 92 | `node.blocked_high_risk` / node `n7` / `approval_rejected_or_executor_unavailable` |
| 93 | task.mark_partial / `PARTIALLY_COMPLETED` |

当前后端已经具备：

- owner-scoped `DomainEvent` 持久化；
- Task SSE endpoint；
- `Last-Event-ID` / `after_id` 回放；
- 单调 event id 与断线重连基础。

当前缺口：

- Workflow 没有为每个节点稳定记录 started/progress/completed/blocked/failed；
- `NodeRun` / `NodeAttempt` 没有成为本次真实执行事实；
- 空输入的 executor 可返回 `OK + 0`，且通常没有解释性事件；
- `NODE_EXECUTOR_UNAVAILABLE` 与审批拒绝共用模糊的 high-risk blocked 事件；
- `TaskChatView` 没有连接 SSE，只显示聚合 run state；
- `events.api.ts` 与 `useTaskEvents.ts` 的前端事件枚举未覆盖执行阶段事件。

## 6. 唯一因果链

```text
命名来源但无 URL
  → Goal Understanding 判为 SPECIFIED_SOURCE
  → seed_urls=[]
  → Spec schema 缺少跨字段不变量
  → Plan 省略 source_search
  → Plan Validator 只验证结构/DAG/参数，未验证首个输入可物化
  → Workflow 自动启动
  → ensure_run_started 无 seed 可写入 frontier
  → 各 executor 在空资源上返回 OK + 0
  → generate_artifact 没有 Production executor
  → Workflow 把 executor unavailable 当成 blocked 后继续
  → Completion 将 0/0 错映射为 PARTIALLY_COMPLETED/access_limited
  → Task Chat 不消费执行事件，只向用户展示 running → partial 与 0/0
```

## 7. 根因分类

| 编号 | 严重度 | 分类 | 根因 |
| --- | --- | --- | --- |
| TASK25-SOURCE-001 | P0 | INVALID_EXECUTION_ADMISSION | `SPECIFIED_SOURCE` 允许空 `seed_urls`，命名来源未被解析或阻断 |
| TASK25-PREFLIGHT-001 | P0 | MISSING_PREFLIGHT | Plan `VALID` 不等于 Execution Ready，启动前没有资源与 executor 支持检查 |
| TASK25-EXECUTOR-001 | P1 | HALF_WIRED | 标准 Plan 包含 `generate_artifact`，Production registry 无实现 |
| TASK25-COMPLETION-001 | P0 | SEMANTIC_MISCLASSIFICATION | 零初始资源/执行器缺失被表示为 partial，而不是 typed blocked/failed |
| TASK25-EVENTS-001 | P1 | OBSERVABILITY_GAP | 缺少统一 node lifecycle 事实，Task Chat 未使用已有 SSE |

## 8. 排除项

以下假设已被 Production 证据排除：

- Temporal Workflow 未启动；
- Worker 离线或 Task Queue 无 poller；
- 队列 backlog 导致任务未执行；
- Task 25 因 Tavily 配置缺失而无法搜索；
- `fetch.url_template` 已经被执行器当作真实 URL 使用；
- 用户看到的 `0/0` 代表系统没有运行任何 Activity。

## 9. 修复门禁

修复必须同时满足：

1. 不可执行来源在 Workflow start 前被解析或阻断。
2. Execution Preflight 证明首个资源可物化、全部 node type 有 Production executor。
3. `generate_artifact` 使用现有 ArtifactService 的真实、幂等实现。
4. 零候选、零匹配、实际部分完成、执行器缺失分别使用不同 typed outcome。
5. 每个真实节点执行都有持久化 lifecycle 事实，且不泄露 chain-of-thought、credential、页面私密正文或敏感参数。
6. Task Chat 使用现有 DomainEvent/SSE 呈现历史快照与实时增量，刷新与断线重连不丢事件、不重复事件。
7. Staging 和 Production 用真实浏览器验证执行进度与最终语义，不以数据库手工修正或服务器源码热改作为验收。

详细方案见 `docs/superpowers/specs/2026-08-15-execution-readiness-progress-design.md`。
