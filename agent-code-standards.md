# 网页信息采集 Agent：代码规范

> 版本：v1.0  
> 日期：2026-08-10  
> 适用范围：Vue 3 前端、FastAPI API、Pydantic AI Agent、Temporal Workflow/Activity、HTTP/Scrapy/Playwright Worker、PostgreSQL、S3/MinIO 集成代码。  
> 工程原则：**开发速度优先，但关键可靠性边界必须自动化守住。**

---

## 1. 规范目标

本规范用于约束后续开发 Agent 和人工开发者的代码实现方式。目标不是追求大型团队级别的繁重治理，而是确保：

1. 模块之间边界清晰，可独立理解、测试和替换。
2. 关键业务状态、幂等、Checkpoint、用户数据隔离不会因为快速开发被破坏。
3. 前端、API、Temporal Worker、Crawler/Browser Worker 能通过稳定契约联动。
4. 代码在本地、CI、Staging、Production 使用同一事实来源，不依赖“服务器现场修代码”。
5. 规范尽量通过格式化器、Linter、类型检查和测试自动执行，减少人工维护成本。

---

## 2. 总体代码组织规则

采用 Monorepo，遵循“模块化单体 + 可独立部署 Worker”设计。

```text
repo/
├─ frontend/
├─ backend/
├─ infra/
├─ docs/
└─ .github/workflows/
```

### 2.1 模块边界

- `frontend` 只通过 API/SSE 与后端通信，不直接访问 PostgreSQL、Temporal、MinIO 私有端点。
- `api` 负责认证、所有权校验、命令/查询、SSE、审批、导出和下载，不在 HTTP 请求里同步执行长任务。
- `workflows` 负责长任务编排和执行语义，不直接承担页面抓取、模型调用和文件副作用。
- 所有网络、浏览器、模型、文件等副作用必须位于 Temporal Activity 或受控 Worker 内。
- Provider 适配器不得直接写 Task 状态。
- 抓取器不得直接决定数据是否进入正式 CSV。
- PostgreSQL 是业务事实来源；Temporal History 是执行位置与恢复事实来源。
- 禁止为了“少写几个文件”把路由、业务规则、数据库 SQL、模型调用、状态转换全部堆在一个文件中。

### 2.2 依赖方向

推荐依赖方向：

```text
API / Workflow / Worker
        ↓
Application Service
        ↓
Domain / State Machine
        ↓
Repository / Provider Protocol
        ↓
Infrastructure Adapter
```

禁止领域层反向依赖 FastAPI Route、Vue 页面、Playwright 实现或具体第三方 SDK。

---

## 3. 前端 Vue 3 / TypeScript 规范

### 3.1 基础要求

- 必须使用 TypeScript。
- 开启 `strict`。
- 使用 Vue 3 Composition API。
- 页面按 feature 组织，不按 `components/services/utils` 无限平铺。
- 共享组件放在明确的 shared/ui 层，业务组件保留在对应 feature 内。
- 页面组件负责组合，不承载复杂业务状态机。
- 服务器返回的 `allowed_actions` 是按钮可用性的事实来源，前端不得复制一套完整后端状态机。

### 3.2 命名

- Vue 组件：`PascalCase.vue`
- composable：`useXxx.ts`
- store：`xxx.store.ts`
- API client：`xxx.api.ts`
- DTO/type：`XxxDto` / `XxxResponse` / `XxxCommand`
- 常量：`UPPER_SNAKE_CASE`
- 普通变量/函数：`camelCase`

### 3.3 页面状态

每个异步页面至少明确区分：

```text
idle
loading
success
empty
error
```

不得仅用一个 `loading: boolean` 覆盖所有业务状态。

### 3.4 SSE

- SSE 只用于服务器推送任务事件/增量结果，不作为业务事实存储。
- 前端断线重连后必须能够通过查询 API 恢复当前状态。
- SSE Event 必须有稳定 `event_type`、`task_id`、`event_id`、`occurred_at`。
- 不依赖“某条 SSE 一定只发送一次”。

### 3.5 UI 文案与业务状态

- 不展示虚构的任务完成百分比。
- `PAUSING / CANCELLING / WAITING_RESOURCE` 等真实中间状态必须如实展示。
- 金额、费用预算、收费、余额等当前版本不进入 UI。
- Token/请求数仅可作为技术诊断指标。

---

## 4. Python / FastAPI / Worker 规范

### 4.1 基础要求

- 使用类型标注。
- 公共接口、DTO、Workflow/Activity 输入输出必须完整类型化。
- FastAPI Route 只做：认证/依赖注入、参数验证、调用 application service、映射响应。
- Route 中禁止直接写复杂 SQL、状态机转换、Provider SDK 调用。
- Pydantic Model 用于边界数据校验，不承担数据库实体全部职责。

### 4.2 命名

- 模块/文件：`snake_case.py`
- 类：`PascalCase`
- 函数/变量：`snake_case`
- 常量：`UPPER_SNAKE_CASE`
- Protocol/接口：以职责命名，如 `ModelProvider`, `SearchProvider`, `EvidenceStore`
- Command/Query：`CreateTaskCommand`, `GetTaskQuery`
- Temporal 输入输出：`XxxWorkflowInput`, `XxxActivityResult`

### 4.3 函数与类

- 一个函数只承担一个主要职责。
- 复杂条件优先提取为具名函数，不堆叠深层 `if/else`。
- 不以“Manager”“Helper”“Utils”作为承载大量无关逻辑的垃圾桶类。
- 工具函数如果仅服务某个 feature，应留在 feature 内。
- 避免全局可变状态。

---

## 5. Domain / 状态机规范

这是强制质量门禁，不属于“可后补”内容。

### 5.1 状态变化

所有 Task/Run/Node 等业务状态变化必须经过：

```text
Command
  ↓
State Machine / Domain Service
  ↓
校验 allowed transition
  ↓
事务写当前状态 + append-only event + outbox
```

禁止：

- LLM 直接写状态。
- Worker 随手 UPDATE Task 状态。
- 根据文字日志反推当前业务状态。
- 前端自行决定状态转换是否合法。

### 5.2 `allowed_actions`

API 返回的 `allowed_actions` 用于告诉前端当前可执行动作。

例如：

```json
{
  "state": "PAUSED",
  "allowed_actions": ["resume", "cancel", "delete"]
}
```

新增状态时必须同步：

1. 状态枚举。
2. 转移规则。
3. `allowed_actions`。
4. 状态事件。
5. 相关测试。
6. 前端显示映射。

---

## 6. Temporal Workflow / Activity 规范

### 6.1 Workflow

Workflow 必须保持确定性。

禁止在 Workflow 内直接：

- 发 HTTP 请求。
- 调 LLM。
- 调 Playwright。
- 读取本地时间作为业务决策。
- 随机生成结果。
- 访问数据库产生不可重放副作用。

这些动作必须放入 Activity。

### 6.2 Activity

每个 Activity 必须明确：

- typed input/output。
- timeout。
- retry policy。
- idempotency key。
- heartbeat 策略（长任务）。
- 可重试错误与不可重试错误。
- 证据/产物引用。
- 用户归属上下文。

### 6.3 重试

只有输入、工具、参数或外部环境至少有一项发生有效变化时，纠错后才允许再次尝试同类失败。

禁止无限重试。

---

## 7. Provider 适配器规范

模型 Provider 和 Search Provider 分开定义协议。

### 7.1 Model Provider

Provider 实现必须隐藏 SDK 差异，对上层暴露统一接口。

不得：

- 把 API Key 写日志。
- 把密钥写 Temporal History。
- 把密钥传给前端。
- 在认证失败时未经授权自动切换其他用户配置。

### 7.2 Search Provider

探索式/混合任务依赖 Search Provider；指定来源任务不依赖。

搜索接口返回统一候选结构，例如：

```text
url
title
snippet
provider
rank
query
```

不得通过抓取搜索引擎结果页伪装成稳定 Search API。

---

## 8. 抓取与证据代码规范

### 8.1 抓取升级

固定按能力阶梯：

```text
API/RSS/Sitemap/Structured Data
→ HTTP/HTML
→ Browser Render
→ Interactive Browser Agent
```

升级必须由可验证证据触发。

### 8.2 robots.txt

- 默认遵守。
- 对公开且无需登录页面允许用户显式覆盖。
- 登录墙、验证码、鉴权、非公开资源不能通过普通覆盖放行。

### 8.3 Evidence

每个最终字段证据至少能关联：

```text
source_url
snapshot_id
quote/dom_locator
extract_method
extractor_version
confidence
```

证据对象只读，不直接在 Evidence Viewer 修改。

---

## 9. 数据库与 Migration 规范

- 所有 schema 变化必须通过 migration。
- 禁止生产服务器手工改表后再“补 migration”。
- Migration 文件进入 Git。
- 破坏性 migration 必须设计兼容窗口。
- 新版本上线时优先采用 expand/contract 思路：
  1. 先新增兼容字段/表。
  2. 新旧代码可共存。
  3. 数据迁移。
  4. 后续版本再删除旧结构。
- 所有用户业务表必须具有不可为空的归属字段或可可靠推导所有权。
- 关键幂等身份必须有数据库唯一约束兜底。

---

## 10. 日志、异常与隐私规范

### 10.1 禁止写入日志

绝对禁止：

- API Key。
- Cookie。
- 密码。
- Authorization Header。
- 表单秘密。
- 完整凭据对象。

### 10.2 结构化日志

推荐字段：

```text
trace_id
user_id
task_id
run_id
node_run_id
provider
error_class
attempt
duration_ms
```

不在普通日志写完整网页正文或完整模型输入输出；使用 Evidence/对象存储引用。

### 10.3 异常分类

至少区分：

- validation
- auth
- permission/ownership
- rate_limit
- network
- provider
- crawl
- extraction
- quality
- storage
- temporal
- internal

不得所有异常统一转成 `500 Internal Server Error` 且丢失可诊断分类。

---

## 11. 测试规范：A-Lite

### 11.1 原则

本项目不追求高覆盖率数字，追求高风险路径有测试。

### 11.2 强制测试区域

以下业务必须有自动化测试：

- 认证与用户数据隔离。
- Task 状态机和 `allowed_actions`。
- CollectionSpec 版本冻结。
- Temporal Workflow 关键路径。
- Activity 幂等。
- Checkpoint/恢复。
- 暂停/取消。
- Provider 密钥边界。
- Record 验证和三分区。
- 去重/冲突。
- CSV 导出范围。
- Evidence 引用。
- 永久删除与对象清理。
- Migration 的升级路径。

### 11.3 不强制高密度单测的区域

- 纯展示 UI。
- 简单 CRUD 页面。
- 无业务分支的样板代码。
- 第三方库自身已经覆盖的行为。

### 11.4 Bug 修复

原则上：

```text
复现 Bug 的测试
→ 测试先失败
→ 修复
→ 测试通过
```

若确实无法稳定自动化复现，必须在 PR 中说明人工复现和验证步骤。

---

## 12. 自动化工具门禁

### 12.1 前端

```text
TypeScript strict
ESLint
Prettier
vue-tsc
Vitest
```

开发阶段优先运行变更相关测试。

模块完成/PR 阶段运行完整：

```text
lint
format-check
type-check
unit/integration tests
build
```

### 12.2 Python

```text
Ruff format
Ruff lint
类型检查
Pytest
```

格式问题优先自动修复，不让 Agent 手工浪费时间调格式。

### 12.3 重型测试

Browser E2E、真实采集、完整环境集成测试不要求每次本地 commit 都执行。

只在：

- 模块完成 Gate。
- PR/CI 必要阶段。
- Staging Deploy Gate。
- Production Release Gate。

执行。

---

## 13. 注释与文档

代码注释解释“为什么”，不要翻译代码。

需要注释的典型场景：

- Temporal 重放约束。
- 幂等设计。
- 兼容 migration。
- 特殊站点策略。
- 安全边界。
- 非直观错误恢复条件。

公共 API、事件 Schema、Provider Protocol、Workflow/Activity 契约发生变化时必须同步更新文档。

---

## 14. Agent 完成代码任务的最低检查清单

后续 Agent 宣布某个模块任务完成前必须确认：

- [ ] 代码仅修改当前模块必要范围。
- [ ] 没有破坏模块边界。
- [ ] 格式/Lint 通过。
- [ ] 类型检查通过。
- [ ] 相关自动化测试通过。
- [ ] 核心业务变更已有测试。
- [ ] 用户归属检查完整。
- [ ] Secrets 未进入日志/前端/Temporal History。
- [ ] Migration 可执行。
- [ ] 跨模块契约已更新。
- [ ] 文档已同步。
- [ ] 达到部署 Gate 时已在服务器环境验证。

任意必选项失败，不得标记模块 `DONE`。
