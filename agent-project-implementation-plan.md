# 网页信息采集 Agent：模块化实施与服务器部署计划

> 本文档是独立的工程实施计划，不属于产品需求决策日志。
> 产品需求与设计事实来源以《网页信息采集 Agent：业务逻辑决策日志》D-001～D-072 为准；若本文档与需求决策日志发生冲突，必须以需求决策日志为准，并先修订实施计划后再继续开发。
> 后续 Codex/Agent 必须按本文档的模块顺序、完成门禁和部署 Gate 执行，不得把“本地代码已写完”视为模块完成。

## 文档职责

- **需求决策日志**：定义产品要做什么、业务规则是什么、页面/UX 如何表现、哪些安全和数据边界不可突破。
- **本实施计划**：定义以什么模块顺序完成、模块之间如何联动、每个模块如何验收、何时必须部署到服务器、部署失败如何阻塞后续工作。
- **代码与运行事实**：Git commit、CI 结果、migration version、镜像 tag、Staging/Production Smoke Test 记录是实施状态的证据，不得只靠文字声明 DONE。

---
> 本节用于指导后续 Codex/Agent 按模块逐步实现项目。它不是新的产品需求来源，而是对 D-001～D-072 已确认需求的工程化落地顺序、模块边界、验收门禁和部署节奏进行固化。

## I-001：采用“模块化单体 + 可独立部署 Worker”作为第一版实施架构

- 状态：已确认
- 日期：2026-08-10
- 目标：体现明确的服务边界和微服务设计思想，但不把第一版拆成大量独立服务，避免分布式事务、服务发现、跨服务版本治理和复杂运维过早进入项目。
- 代码组织：采用单一 Git 仓库（Monorepo），前端、后端、Worker、基础设施配置统一版本管理。
- 运行时边界固定为：
  1. `web`：Vue 3 前端，提供登录、工作台、任务、模板、模型/搜索配置、设置、Task 对话/数据/质量、执行详情和证据查看器。
  2. `api`：FastAPI，负责认证、用户数据归属校验、命令/查询接口、SSE、Provider 配置、审批、导出和下载。
  3. `worker`：统一 Python Worker 代码库，通过不同 Temporal Task Queue/启动参数运行不同资源角色，而不是复制多套 Worker 代码：
     - orchestration / core activity worker。
     - HTTP/Scrapy worker。
     - Browser/Playwright worker。
     - LLM/Search provider worker。
  4. 基础设施依赖：PostgreSQL、Temporal Server、S3 兼容对象存储（开发 MinIO）、OpenTelemetry Collector、反向代理/HTTPS 入口。
- 服务通信原则：
  - 浏览器只调用 `api`，不直接访问数据库、Temporal 或对象存储私有端点。
  - API 不把长任务在 HTTP 请求中同步跑完，只提交命令并启动/Signal Temporal Workflow。
  - Worker 不直接信任前端参数，所有 Activity 输入必须来自已校验的任务/计划/节点契约。
  - PostgreSQL 是业务事实来源；Temporal History 是执行位置与恢复事实来源；两者继续遵守 D-027 的职责边界。
- 第一版不引入 Kubernetes、服务网格、独立消息总线微服务、独立认证服务、独立证据服务或独立搜索微服务；若未来容量或团队规模需要，再从现有模块边界中拆分。

### 推荐仓库结构

```text
repo/
├─ frontend/                       # Vue 3
│  ├─ src/app/                     # Router、App Shell、全局 Store
│  ├─ src/features/auth/
│  ├─ src/features/workbench/
│  ├─ src/features/tasks/
│  ├─ src/features/templates/
│  ├─ src/features/providers/
│  ├─ src/features/settings/
│  ├─ src/features/data/
│  ├─ src/features/quality/
│  ├─ src/features/execution/
│  └─ src/features/evidence/
│
├─ backend/
│  ├─ app/api/                     # FastAPI routes、dependencies、SSE
│  ├─ app/auth/                    # session、password、ownership guard
│  ├─ app/domain/                  # Task/Spec/Plan/Run/Record 等领域对象
│  ├─ app/state/                   # 状态机、allowed_actions、领域事件
│  ├─ app/agents/                  # Pydantic AI 目标理解、规划、语义提取
│  ├─ app/providers/               # ModelProvider / SearchProvider 适配器
│  ├─ app/workflows/               # Temporal Workflow
│  ├─ app/activities/              # Activity 实现
│  ├─ app/crawling/                # URL frontier、robots、HTTP/Scrapy/Browser
│  ├─ app/extraction/              # 规则/LLM 提取
│  ├─ app/validation/              # 验证、冲突、去重、质量
│  ├─ app/evidence/                # snapshot / FieldEvidence
│  ├─ app/artifacts/               # CSV / 导出 / 下载
│  ├─ app/storage/                 # PostgreSQL/S3 repository 与事务
│  └─ tests/
│
├─ infra/
│  ├─ compose/                     # local/staging/production compose
│  ├─ reverse-proxy/               # HTTPS 入口配置
│  ├─ otel/                        # Collector 配置
│  └─ scripts/                     # migration、backup、restore、smoke
│
├─ docs/
│  ├─ architecture/
│  ├─ api/
│  ├─ operations/
│  └─ runbooks/
└─ .github/workflows/              # CI / image build / deploy（若使用 GitHub）
```

## I-002：后续 Agent 必须按“单模块闭环”工作，不允许跨模块随意并行扩散

- 状态：已确认
- 日期：2026-08-10
- Agent 每次只领取一个当前模块；只有当前模块满足全部完成门禁后，才能把模块状态从 `IN_PROGRESS` 改为 `DONE` 并开始下一模块。
- 每个模块必须同时完成以下闭环：
  1. **前置条件确认**：依赖模块已完成，数据库/API/事件契约版本明确。
  2. **设计核对**：只使用本决策日志中已确认需求；发现冲突时先记录冲突，不得静默创造新产品规则。
  3. **实现**：代码、迁移、配置、前端交互、Worker/Workflow 逻辑按该模块范围完成。
  4. **自动化测试**：单元测试、契约测试、必要的集成测试先通过。
  5. **联动测试**：至少验证一个上游输入和一个下游消费契约；没有下游实现时用契约测试固定接口。
  6. **安全检查**：用户归属、秘密值、日志脱敏、幂等、状态机权限与错误路径按模块适用范围验证。
  7. **文档**：更新 API/事件/数据模型/运行手册；模块涉及决策变化时回写本日志，不允许只改代码。
  8. **验收证据**：保存测试命令、结果摘要、关键截图/日志/接口样例。
  9. **提交**：形成可独立审查和回退的 Git commit/PR。
  10. **部署门禁**：若当前模块属于部署检查点，必须完成服务器部署与 Smoke Test 后才算 `DONE`。
- 模块状态统一使用：`NOT_STARTED / IN_PROGRESS / BLOCKED / DONE / DEPLOYED`。
- Agent 禁止行为：
  - 为了完成当前模块而偷偷实现未来模块的大量功能。
  - 用临时假数据绕过真实业务契约后直接宣称完成。
  - 测试失败但继续进入下一模块。
  - 修改已确认的状态机、页面边界、数据隔离或安全边界而不记录替代决定。
  - 只完成前端按钮但没有真实后端命令，或只完成后端接口但没有对应使用链路。

## I-003：模块之间采用稳定契约联动，避免隐式耦合

- 状态：已确认
- 日期：2026-08-10
- 模块之间优先通过以下稳定契约联动：
  - FastAPI Command/Query DTO。
  - PostgreSQL 领域对象与版本字段。
  - Domain Event / Outbox Event。
  - Temporal Workflow/Activity typed input/output。
  - SSE Event schema。
  - Provider adapter protocol。
  - 对象存储 `artifact_id/evidence_id/content_hash` 引用。
- 前端不得依赖 Worker 内部实现；Worker 不依赖 Vue 页面结构；Provider 适配器不直接写 Task 状态；抓取器不直接决定 Record 是否进入正式 CSV。
- 所有跨模块 ID 都必须携带 `user_id`/资源归属边界或由服务端上下文可靠推导，避免跨用户引用。
- 所有会被后续模块消费的契约，在生产代码上线后不得随意改名或改变语义；破坏性变更必须使用版本字段、迁移或兼容层。

# 实施阶段总览

```text
阶段 A：工程与业务底座
M-01 → M-02 → M-03 → M-04
                 ↓
            DEPLOY-GATE-1
            首次服务器 Staging

阶段 B：用户可创建任务并进入可靠工作流
M-05 → M-06 → M-07 → M-08
                 ↓
            DEPLOY-GATE-2
            可交互任务 Staging

阶段 C：真实网页采集端到端
M-09 → M-10 → M-11 → M-12
                 ↓
            DEPLOY-GATE-3
            真实 E2E 采集 Staging

阶段 D：完整产品操作闭环
M-13 → M-14 → M-15
          ↓
     DEPLOY-GATE-4
     Release Candidate

阶段 E：可靠性、生产安全与正式发布
M-16 → M-17 → M-18
                 ↓
            DEPLOY-GATE-5
            Production Release
```

---

# M-01：工程骨架、本地基础设施与质量门禁

## 目标

建立后续所有模块共同依赖的可运行工程骨架，使新 Agent 在本机可以一条命令启动 Vue、FastAPI、PostgreSQL、Temporal、MinIO 和 OpenTelemetry，并有统一测试/格式化/迁移机制。

## 前置依赖

- 无。

## 必须完成

- 建立 Monorepo 目录结构并固定 Python/Vue 包管理方式。
- FastAPI 提供 `/health/live`、`/health/ready`。
- Vue 3 建立 Router、全局错误边界、API Client、基础布局壳。
- PostgreSQL 迁移框架可执行升级/回滚。
- Temporal Client 与 Worker 能连接本地 Temporal Server，并运行最小测试 Workflow。
- MinIO/S3 client 可以写入、读取、按 content hash 定位测试对象。
- OpenTelemetry 将 API 请求与最小 Workflow/Activity 关联到同一个 trace。
- 建立本地 `docker compose`，服务启动有健康检查与依赖顺序。
- 建立 CI：后端测试、前端测试、lint/type-check、migration consistency。
- 秘密配置只允许从环境变量/Secret file 注入，示例配置不得包含真实 Key。

## 产出契约

- `Settings`：统一环境配置入口。
- `DatabaseSession`/repository 基础设施。
- `TemporalClientFactory`。
- `ObjectStorage` interface。
- `/health/live`、`/health/ready`。

## 自动化验收

- 全新机器执行本地启动脚本后所有服务健康。
- API readiness 在 PostgreSQL/Temporal/Object Storage 不可用时必须失败，而 live 仍能反映进程存活。
- 运行一个测试 Workflow → Activity → PostgreSQL 写测试记录 → MinIO 写测试对象 → API 查询成功。
- CI 在故意制造 lint、test、migration 错误时能正确阻止合并。

## 完成门禁

- 本地一键启动成功。
- CI 全绿。
- `docs/architecture/local-dev.md`、`docs/operations/local-run.md` 完成。
- 形成独立提交。

## 与后续联动

- M-02～M-18 均依赖本模块的配置、数据库、Temporal、对象存储和 CI 基线。

---

# M-02：注册登录、Session、安全边界与用户数据隔离

## 目标

完成 D-020～D-023、D-054～D-057 对注册登录和用户私有隔离的底层能力，使任何业务对象从第一天开始都不能跨用户访问。

## 前置依赖

- M-01。

## 必须完成

- `/register`、`/login` 对应后端注册/登录接口。
- 邮箱唯一，密码安全哈希，第一版不做邮箱验证、不做忘记密码、不做 OAuth。
- Secure/HttpOnly/SameSite Session Cookie；开发环境与生产环境安全参数可配置。
- 登录限流与认证失败统一错误，不泄漏账号是否存在等不必要信息。
- `CurrentUser` dependency 与统一 ownership guard。
- 所有 repository 查询接口从设计上要求用户上下文或 owner 条件，不允许默认全表查询。
- `设置 → 账户资料/安全` 所需最小接口：显示邮箱/显示名称、修改密码、退出当前会话、退出其他会话。
- 跨用户访问统一返回不可推断资源存在性的安全响应。

## 产出契约

- `User`、`Session` 数据模型。
- `require_user()`。
- `assert_owned(resource, user_id)` 或等价统一 ownership policy。
- 注册、登录、登出、修改密码、会话列表/撤销 API。

## 自动化验收

- 用户 A 与用户 B 分别注册后，A 无法读取/修改/删除 B 的任何测试资源。
- Session 失效后所有业务路由拒绝访问。
- 修改密码后按策略使旧会话失效。
- 登录限流生效。
- 前端未登录访问业务路由自动进入 `/login`，登录后进入 `/app`。

## 完成门禁

- 认证 E2E 测试通过。
- 跨用户隔离测试成为永久回归测试。
- 安全 Cookie 配置文档完成。

## 与后续联动

- M-03 的密钥、M-04 的 Task/Record、M-05 之后全部页面都必须复用该用户边界。

---

# M-03：BYOK 模型、Search Provider 与秘密凭据管理

## 目标

完成 D-029、D-049、D-066、D-069 与凭据安全要求，建立统一 Provider Registry 和加密凭据能力。

## 前置依赖

- M-01、M-02。

## 必须完成

- 信封加密：主密钥与数据库分离；数据库只保存密文、key reference、必要元数据。
- `ModelProvider` 统一接口与首批 Provider 适配器注册能力。
- `SearchProvider` 独立接口，不与 Model Provider 混成同一个 DTO。
- `/models` 页面支持“AI 模型 / 搜索服务”两个配置区；新增/编辑使用 Drawer。
- Model/Search 配置可测试连接，返回稳定错误分类：认证失败、模型不存在、限流、网络失败等。
- API Key、Cookie、密码不得被读取回前端明文；UI 只能看到“已配置”和脱敏元数据。
- 默认 ModelConfig 只影响未来任务。
- 无 ModelConfig 时触发 `MODEL_NOT_CONFIGURED`；探索式/混合式任务无 Search Provider 时触发 `SEARCH_PROVIDER_NOT_CONFIGURED`。
- 建立通用 `Credential` 对象，为后续网站 Cookie/用户名密码复用。

## 产出契约

- `ModelProvider` protocol。
- `SearchProvider` protocol。
- `CredentialVault` interface：`store_secret/ref/read_for_execution/rotate/delete`。
- `ModelConfig`、`SearchConfig`、`Credential` 数据模型与版本/owner 信息。
- `ProviderTestResult` 稳定 DTO。

## 自动化验收

- 存储 Key 后数据库看不到明文；日志、Temporal History 测试 fixture 中不得出现秘密值。
- 用户 A 无法使用用户 B 的 Provider 或 Credential。
- Provider 测试成功/认证失败/网络失败路径均有自动化测试。
- 删除/轮换 Key 不影响历史任务审计元数据，但运行任务引用按规则被保护或进入待处理。

## 完成门禁

- Provider 契约测试通过。
- Secrets scan 无明文泄漏。
- 前端新增、测试、编辑、更换 Key 闭环可用。

## 与后续联动

- M-06 使用 Model Provider 做目标理解；M-09 使用 Search Provider；M-10 使用网站 Credential；M-11 使用模型做语义提取。

---

# M-04：核心领域数据模型、状态机、事件、幂等与 Checkpoint 基础

## 目标

把 D-004～D-008、D-011、D-015、D-016、D-030 的核心业务事实固化为数据库和领域服务，为后续所有任务执行建立唯一可信状态模型。

## 前置依赖

- M-01～M-03。

## 必须完成

- 建立核心表/模型：Task、CollectionSpecVersion、PlanVersion、Run、NodeRun、NodeAttempt、URLResource、PageSnapshot 索引、Record、FieldEvidence 索引、Approval、Artifact、DomainEvent、IdempotencyKey、Outbox、Checkpoint。
- 所有用户业务表有不可为空 owner/user 归属。
- 建立 Task 与 Node 状态机、乐观锁 `version`、`allowed_actions` 计算。
- 状态变化、业务写入、领域事件、Outbox 在同一事务提交。
- 建立 API request idempotency、Node batch idempotency、Artifact identity 基础函数。
- Checkpoint 只在批次业务事务成功提交后生成；Temporal heartbeat 不冒充业务 Checkpoint。
- 建立软删除/已删除状态基础，永久删除留给 M-15 完成对象级联。

## 产出契约

- 领域 repository + service。
- `transition_task(command)`、`transition_node(command)` 或等价状态命令接口。
- `AllowedAction[]`。
- `append_domain_event()`。
- `commit_checkpoint()`。
- `idempotency_key_for_*()` 统一函数。

## 自动化验收

- 非法状态转换被拒绝。
- 同一幂等键重复请求只产生一次有效结果。
- 模拟事务中断后不会出现“数据已写但状态未变”或“状态已变但事件未写”的半完成状态。
- 两个并发更新命中乐观锁冲突时不会静默覆盖。
- Checkpoint 重放可识别已提交批次。

## 完成门禁

- 数据库迁移和回滚测试通过。
- 核心状态机测试覆盖所有允许/禁止边。
- 所有核心业务对象有 owner 隔离测试。

## 与后续联动

- M-05 起所有 UI 状态来自此模型；M-07 Temporal、M-08 Plan/Approval、M-09～M-12 采集链路全部写入该状态与事件体系。

---

# DEPLOY-GATE-1：首次服务器 Staging 部署（M-01～M-04 后强制执行）

## 目的

尽早验证“本地能跑”不等于“服务器能跑”，把网络、容器、HTTPS、持久化卷、迁移、密钥和进程恢复问题提前暴露。

## 推荐拓扑

第一阶段不使用 Kubernetes，采用单服务器 Docker Compose，并为 staging 建立独立环境：

```text
Internet
  ↓ HTTPS
Reverse Proxy
  ├─ web-staging
  └─ api-staging
       ├─ PostgreSQL-staging
       ├─ Temporal-staging
       ├─ MinIO/S3-staging
       └─ OTel Collector

worker-* 通过内网访问 PostgreSQL/Temporal/Object Storage
```

## 必须完成

- 服务器创建独立 `staging` 配置和持久化目录；禁止与未来 production 共用数据库/schema/bucket。
- 配置 HTTPS、可信反向代理、安全 Cookie、CORS/CSRF 基线。
- secrets 不进 Git；服务器以 Secret file/环境注入。
- 执行数据库迁移并保存迁移日志。
- 部署 web/api/worker 与基础设施容器。
- 配置进程/容器自动重启。
- 建立最小备份脚本：PostgreSQL + 对象存储元数据/必要对象。

## Smoke Test

1. 注册两个测试用户。
2. 登录/登出成功。
3. 创建并读取各自测试资源，验证跨用户拒绝。
4. 保存一个测试 Provider Key，数据库/日志无法看到明文。
5. 运行测试 Temporal Workflow 并成功写 Checkpoint。
6. 重启 API/Worker 容器后数据与 Workflow 连接正常。

## 失败处理

- 任一 Smoke Test 失败：M-04 不得标记 `DEPLOYED`，M-05 不得开始。
- 数据库迁移失败：回滚到上一镜像与上一兼容 migration 状态，不手工修改生产式数据库结构“修过去”。

---

# M-05：Vue 全局 App Shell、13 类页面骨架与真实导航

## 目标

把 D-031～D-067 已确认 UI/UX 架构落成可导航、可鉴权、可接真实 API 的前端框架，而不是静态 Demo。

## 前置依赖

- M-01～M-04 且 DEPLOY-GATE-1 通过。

## 必须完成

- 全局 App Shell、可折叠侧边栏、用户菜单。
- 13 类路由完整注册：login/register/app/tasks/templates/template-edit/models/settings/task chat/data/quality/execution/evidence。
- Task 顶部只保留“对话 / 数据 / 质量”。
- Overlay Drawer 基础设施：Task Status、Approval、Credential、Record、Evidence Quick、Node Detail、Provider Edit。
- Modal/Sheet 基础设施：CollectionSpec Editor、Template Variable、Export、Delete Confirm、Model Required。
- 工作台、我的任务、模板、模型配置、设置页面与已存在后端接口真实联通；尚未实现的 Task 业务区域使用明确 empty state，而不是假数据。
- 统一 Deep Link 解析框架，如 `?approval=`、数据筛选 query。
- 全局 API error mapper，至少处理认证失效、模型未配置、搜索未配置、资源不存在/无权、冲突、服务暂不可用。

## 产出契约

- Router map。
- `ApiClient`。
- `CurrentUserStore`、`TaskShellStore`、`DrawerStore` 等最小 Store。
- `allowed_actions` 驱动按钮显隐/禁用的统一组件机制。

## 自动化验收

- 路由守卫测试。
- 侧边栏收起状态不影响业务状态。
- 深链接进入无权限 Task 时不会泄漏 Task 信息。
- 按后端 `allowed_actions` 驱动操作，不在多个组件中复制状态判断。

## 完成门禁

- 13 类路由都可访问/受鉴权。
- 前端无硬编码假业务数据依赖。
- Playwright/Cypress 级基础导航 E2E 通过。

## 与后续联动

- M-06～M-15 逐步把真实业务能力填入现有页面/Drawer，不再新增一级页面。

---

# M-06：Task Draft、Agent 对话、CollectionSpec 与模板闭环

## 目标

完成“用户从自然语言开始 → Agent 理解 → CollectionSpec → 用户确认 → 规格冻结”的第一条核心业务链。

## 前置依赖

- M-02～M-05。

## 必须完成

- `+ 新任务` 创建空 Task Draft 并进入 `/tasks/:id/chat`。
- 工作台直接输入创建 Task + 第一条消息并跳转 Chat。
- Chat message 持久化与用户归属。
- Pydantic AI 目标理解：识别探索式/指定来源/混合式，生成 typed CollectionSpec Draft。
- 低置信度/关键歧义返回澄清问题；高置信度按 D-004 进入确认策略。
- CollectionSpec 摘要卡与完整 Editor Sheet。
- 字段类型、必填、自动扩展字段策略、范围、完成条件、高级运行限制。
- Spec 确认创建不可变版本；执行开始后禁止原地修改。
- `/templates`、模板编辑、模板变量、从成功/合适任务保存为模板的后端/前端契约。
- Task 使用模板时保留 Template Version 引用，但运行事实以生成的 CollectionSpec Version 为准。

## 产出契约

- `TaskDraftService`。
- `ChatMessage`。
- `CollectionSpecDraft/CollectionSpecVersion` typed schema。
- `CollectionTemplate/TemplateVersion`。
- `confirm_spec(task_id, spec_version)` 命令。

## 自动化验收

- 三类任务输入分别产生正确 task type。
- 修改已冻结 Spec 必须创建新版本。
- 模板修改不影响历史 Task。
- 未配置模型时保留输入并正确进入 Model Required 流程。
- 用户 A 不能引用用户 B 的模板。

## 完成门禁

- 以真实 Provider 完成至少一次“自然语言 → Spec → 确认”E2E。
- Spec JSON Schema/数据库约束测试全绿。

## 与后续联动

- M-07 只接收已确认的 Spec Version 启动 Workflow；M-08 以 Spec 为业务约束生成 Plan。

---

# M-07：Temporal Task Workflow、暂停/恢复/取消与 SSE 事件流

## 目标

建立可靠长任务执行骨架，使任务可以启动、等待、暂停、恢复、取消、崩溃恢复，并把可信状态实时推送前端。

## 前置依赖

- M-04、M-06。

## 必须完成

- `TaskWorkflow` typed input 只携带 task/spec/run/version IDs，不携带秘密明文。
- Workflow 启动时创建 Run，并校验 Spec 已冻结。
- Signal/Update：pause、resume、cancel、approval resolution、用户改向前安全暂停。
- Worker Activity heartbeat 和 NodeRun/Attempt 记录。
- 小批次 Activity 在业务事务后创建 Checkpoint。
- Worker crash/restart 后 Workflow 继续；已提交批次不重复生效。
- API 提供 Task command endpoints，所有命令经过状态机和幂等校验。
- SSE：输出任务状态、阶段、计数、重要事件、审批、暂停/恢复、完成/失败事件；连接重建可通过 event id/时间线补发而不是丢状态。
- Task Status Drawer 使用真实 SSE/Query 数据。

## 产出契约

- `TaskWorkflow`。
- `TaskCommandService`。
- `SSETaskEvent` schema。
- `pause_task/resume_task/cancel_task`。

## 自动化验收

- 执行中暂停：停止调度新工作，当前安全单元提交后进入 PAUSED。
- Worker 强制退出后重启，从最后 Checkpoint 恢复。
- 重复 pause/cancel 命令幂等。
- SSE 断线重连后当前状态一致。

## 完成门禁

- Temporal integration tests 通过。
- 前端能够看到“运行中/暂停中/已暂停/取消中/已取消”的真实过渡。

## 与后续联动

- M-08 将 Plan 节点交给 Workflow 调度；M-09～M-12 以 Activity 形式挂入本执行骨架。

---

# M-08：Plan 生成、节点注册表、确定性校验与人工审批

## 目标

实现 D-007、D-008、D-017 的“Agent 可规划但不能越界”机制。

## 前置依赖

- M-04、M-06、M-07。

## 必须完成

- 建立 Node Registry：SourceSearch、AccessRulesCheck、LinkDiscovery、Fetch、BrowserRender、Extract、Normalize、Deduplicate、Validate、GenerateArtifact 等标准节点契约。
- 每个节点声明 typed input/output、timeout、retry policy、permission level、idempotency identity、recoverable boundary、resource class。
- Pydantic AI Plan Generator 只允许引用注册节点。
- Deterministic Plan Validator 检查 DAG、依赖、参数、范围、权限、运行限制、Spec Version。
- Plan Version 持久化；重规划产生新版本并记录旧/新差异、触发证据和影响范围。
- Approval 对象、Drawer、Deep Link、Temporal 等待与恢复。
- 低风险操作自动执行；高风险和超出授权范围操作等待用户确认；授权绑定 Spec Version/参数指纹/范围/失效条件。
- robots 显式覆盖审批可复用通用 Approval 契约，但类型单独记录。

## 产出契约

- `NodeDefinition` registry。
- `PlanGraph/PlanVersion`。
- `validate_plan()`。
- `ApprovalRequest/ApprovalDecision`。
- `request_approval()/resolve_approval()`。

## 自动化验收

- Agent 生成未注册节点时 Plan Validator 拒绝。
- 扩大域名范围/改变字段含义/降低质量标准时必须阻塞并生成审批或新 Spec。
- Approval 参数改变后旧授权失效。
- 用户拒绝后 Workflow 走合法替代路径或把节点标为不可处理，而不是偷偷执行。

## 完成门禁

- 至少 10 组合法/非法 Plan fixture 契约测试。
- Chat 审批卡、Task Drawer 待审批、Deep Link 都能落到同一 Approval 对象。

## 与后续联动

- M-09～M-12 的所有执行能力必须先注册为节点，不允许绕过 Plan Registry 直接被 Agent 调用。

---

# DEPLOY-GATE-2：可交互 Task Workflow Staging（M-05～M-08 后强制执行）

## 必须验证的用户闭环

1. 注册/登录。
2. 配置 Model Provider。
3. 工作台创建任务。
4. Agent 生成 CollectionSpec。
5. 用户确认。
6. 生成并校验 Plan。
7. Temporal Workflow 启动。
8. 前端 SSE 看到状态变化。
9. 触发一个模拟高风险节点并完成 Approval。
10. 暂停、恢复、取消均成功。

## 部署要求

- CI 构建版本化 Docker image；推荐推送到受控镜像仓库，再由服务器 `docker compose pull && up -d`，避免服务器现场产生不可复现构建。
- 每次部署记录 `git_sha/image_tag/migration_version/deploy_time`。
- 数据库迁移先做兼容检查；API/Worker 镜像与 migration 版本要匹配。
- Smoke Test 失败自动/人工回退到上一个稳定 image tag。

## 门禁

- 上述闭环未在服务器 Staging 完整跑通，不进入 M-09。

---

# M-09：外部搜索、robots、站内 Link Discovery 与 URL Frontier

## 目标

完成 D-068～D-070 的来源发现两阶段，实现探索式/混合式任务真正能找到候选站点并在站内安全扩展 URL。

## 前置依赖

- M-03 Search Provider。
- M-07 Workflow。
- M-08 Node Registry。

## 必须完成

- Search Provider 查询适配与结果标准化。
- Agent 生成搜索词但 Search Activity 有 query/页数/去重/运行限制。
- Seed URL 和指定来源进入同一 Source Candidate 模型。
- robots.txt 下载、缓存、规则解析与默认阻止。
- robots `Disallow` 的公开页面显式覆盖 → Approval + audit。
- Sitemap/RSS/导航/栏目/分页/页面链接发现。
- URL normalize：scheme/host/path/query 规范化、fragment 处理、跟踪参数策略、canonical hint。
- URL Frontier 状态：discovered/eligible/queued/fetched/skipped/blocked/failed 等。
- 域名/路径范围限制，禁止 Link Discovery 无限越界。
- Frontier 批次 Checkpoint 与幂等。

## 产出契约

- `SourceCandidate`。
- `NormalizedUrl`。
- `AccessDecision`。
- `UrlFrontierRepository`。
- SourceSearch/AccessRulesCheck/LinkDiscovery Activity。

## 自动化验收

- 指定来源无需 Search Provider 也能进入站内发现。
- 探索任务无 Search Provider 正确阻塞。
- 重复/等价 URL 不重复入队。
- robots 默认阻止、用户覆盖、禁止覆盖三种路径测试。
- Frontier crash/restart 不丢 URL、不重复提交有效结果。

## 完成门禁

- 使用至少 3 种测试站点 fixture（sitemap、分页、普通导航）完成站内发现 E2E。

## 与后续联动

- M-10 只从已通过 AccessDecision 的 Frontier 项抓取；M-12 用来源覆盖数据生成质量指标。

---

# M-10：网页获取阶梯、Scrapy/HTTP/Playwright、Credential 与快照存储

## 目标

实现 D-009 的固定能力升级阶梯，并把每次网页获取变成可审计、可恢复、可复用的 PageSnapshot。

## 前置依赖

- M-03 CredentialVault。
- M-09 URL Frontier。

## 必须完成

- 获取顺序：公开 API/RSS/Sitemap/结构化入口 → HTTP/HTML → Scrapy 批量 → Playwright 渲染 → Browser Agent（仅注册但第一版按需启用）。
- 升级必须基于可验证证据：正文为空、关键字段缺失、动态加载信号、必须交互。
- HTTP 状态、响应头摘要、content type、下载大小、耗时、redirect chain 脱敏记录。
- PageSnapshot 原始内容按 content hash 上传对象存储；数据库保存 snapshot metadata、hash、tool/version、URL、时间。
- 同站成功策略缓存与有效期；页面结构变化时重新探测。
- 登录/非公开页面按 Credential Drawer + Approval 使用凭据引用，Activity 执行时临时解密。
- 验证码不得自动绕过。
- Browser worker 使用独立低并发资源类。

## 产出契约

- `FetchRequest/FetchResult`。
- `PageSnapshotRef`。
- `SiteFetchStrategy`。
- Fetch/BrowserRender Activity。

## 自动化验收

- 静态页面不启动 Playwright。
- 动态 fixture 能根据证据升级 Playwright。
- 相同内容重抓复用 hash/快照身份或产生明确版本关系。
- Cookie/密码不会出现在日志、Temporal History、普通事件。
- 失败重试符合错误分类，不无限升级/重试。

## 完成门禁

- HTTP、动态页面、登录凭据、失败/重试四类 E2E fixture 通过。

## 与后续联动

- M-11 从 PageSnapshot 做字段提取；M-14 证据查看器读取此快照；M-15 生命周期清理依赖 snapshot 引用关系。

---

# M-11：字段提取、规则学习、LLM Fallback 与 FieldEvidence

## 目标

实现 D-010：优先确定性规则，大模型只解决语义不确定部分，并且每个最终字段都能追溯证据。

## 前置依赖

- M-06 CollectionSpec Schema。
- M-10 PageSnapshot。

## 必须完成

- 提取阶梯：JSON-LD/Meta/Table → 已验证 CSS/XPath/站点规则 → LLM typed extraction。
- 所有 extractor 输出先通过字段名称、类型、枚举、格式 schema 校验。
- LLM 输出失败不能直接写最终 Record。
- 从 LLM 样本推导的站点规则需要代表性页面验证、质量阈值、版本、回退。
- `FieldEvidence` 保存 URL、snapshot id、原文片段或 DOM locator、提取方式、extractor version、confidence。
- 必要时保留支撑字段值的最小原文片段，供重型文件生命周期清理后仍维持基本证据链。
- 提取批次幂等键包含 Task/Spec/URL/schema/extractor version。

## 产出契约

- `Extractor` protocol。
- `ExtractionCandidate`。
- `FieldEvidence`。
- `ExtractorRuleVersion`。
- Extract/Normalize Activity。

## 自动化验收

- 结构化页面走确定性提取且不调用 LLM。
- 不规则页面才调用 LLM。
- LLM 返回错误类型/缺字段时进入失败或待复核路径，不污染 PASSED。
- 每个测试 Record 字段都能找到 Evidence。
- Rule Version 变更可回滚和重算受影响记录。

## 完成门禁

- 至少覆盖结构化、模板规则、LLM fallback 三类站点 fixture。

## 与后续联动

- M-12 验证 Record/Evidence；M-13 Record Drawer 展示证据；M-14 Evidence Viewer 深入审查。

---

# M-12：验证、业务去重、冲突、三类结果、完成判定与质量指标

## 目标

完成 D-006、D-014 的数据可信闭环，让“采到了数据”转化为“可正式导出的可信数据”。

## 前置依赖

- M-04 Record/状态模型。
- M-11 Extraction/Evidence。

## 必须完成

- 验证顺序：结构/类型 → 必填 → 字段证据 → 业务规则 → 去重 → 跨来源冲突 → 分层抽样。
- 任务业务唯一键策略与确定性 dedupe；近似重复如需模型辅助，模型只提供候选，最终规则可审计。
- 跨来源冲突按来源优先级、证据强度、更新时间裁决；无法裁决进入 `NEEDS_REVIEW`。
- 结果固定分区：PASSED / NEEDS_REVIEW / REJECTED。
- `review_type/review_reason/allowed_actions` 生成。
- 分层抽样：来源、提取方式、规则版本、confidence。
- 质量指标：通过率、缺失率、重复率、冲突数、来源覆盖、抽样准确率、待复核数、拒绝数。
- 完成判定：定向范围完成；探索达到最低合格记录 + 信息饱和；达到运行限制/用户停止等进入部分完成。
- 不使用人民币金额预算作为完成条件。

## 产出契约

- `ValidationResult`。
- `ReviewReason/AllowedReviewAction`。
- `QualityMetrics`。
- `CompletionDecision`。
- Deduplicate/Validate Activity。

## 自动化验收

- 同一条业务记录跨来源去重稳定。
- 冲突无法确定时不会静默选值。
- 无 Evidence 的字段无法进入 PASSED（按字段规则允许的例外必须显式定义）。
- 探索饱和 fixture 能正常完成；运行限制触发部分完成。
- 质量指标和分区计数与数据库一致。

## 完成门禁

- 一条真实 Staging Task 能从 SourceSearch 一直跑到 PASSED/NEEDS_REVIEW/REJECTED。

## 与后续联动

- M-13 数据页消费三类结果；M-14 质量页消费指标；M-15 正式 CSV 只消费 PASSED。

---

# DEPLOY-GATE-3：真实端到端采集 Staging（M-09～M-12 后强制执行）

## 目的

第一次在服务器上验证真实网页采集主链，不允许只用本地 fixture 宣称爬虫能力完成。

## 至少执行三类 Staging 任务

1. **指定来源静态站点任务**：HTTP/规则提取即可完成。
2. **探索式任务**：Search Provider → 候选站点 → Link Discovery → Fetch → Extract → Validate。
3. **动态页面任务**：能够从 HTTP 证据升级到 Playwright。

## 必验行为

- robots 默认遵守。
- URL Frontier 不越界。
- 重试/限流有界。
- PageSnapshot/Evidence 可查。
- 三类结果正确。
- 暂停恢复后不重复数据。
- Worker 重启后可继续。
- 两个用户同时运行不会发生数据/凭据串用。

## 门禁

- 三类任务全部能得到可解释终态后才进入 M-13。

---

# M-13：实时数据页、人工审核、批量操作与数据查询能力

## 目标

完成 `/tasks/:id/data` 的真实业务闭环，让用户运行中即可查看并处理数据。

## 前置依赖

- M-05 前端壳。
- M-07 SSE。
- M-12 结果分区。

## 必须完成

- PASSED / NEEDS_REVIEW / REJECTED Tab 与实时计数。
- 后端分页搜索、字段筛选、简单 AND、排序、列设置。
- Record Detail Drawer。
- 单条审核：人工修正、通过、拒绝、让 Agent 重新处理。
- 人工修正保留 original_value/final_value/value_source/modified_at 和原 Evidence。
- 批量审核只对后端 `allowed_actions` 允许且语义兼容记录开放。
- Agent 重新处理生成新的执行尝试/事件，不直接覆盖旧历史。
- 数据页 query 参数可被质量页 Deep Link 复用。

## 产出契约

- Records Query API。
- Review Command API。
- Batch Review API。
- `RecordView/ReviewAction` DTO。

## 自动化验收

- 运行中新增 PASSED 记录前端可增量看到。
- 大数据集搜索/筛选不依赖全量前端加载。
- 人工修正后 Evidence 仍保留原值来源。
- 不兼容 Review Type 批量通过会被后端拒绝。

## 完成门禁

- 真实 Staging Task 中至少处理一条冲突、一条缺失、一条人工修正记录。

## 与后续联动

- M-14 Quality Deep Link 到本页；M-15 导出使用本页当前筛选快照。

---

# M-14：质量页、执行详情、时间线、节点视图与证据查看器

## 目标

把 D-024 和 UI 二级页面完整落地，让用户能解释“Agent 做了什么”和“这条数据为什么是这个值”。

## 前置依赖

- M-07 Domain/SSE events。
- M-11 Evidence。
- M-12 QualityMetrics。
- M-13 Data query。

## 必须完成

- `/quality`：质量指标、字段完整性、来源覆盖、抽样验证；指标可 Deep Link 到 `/data` 筛选视图，不在 Quality 页面重复编辑数据。
- `/execution` 默认“阶段 + 时间线”，可切换只读 Plan DAG。
- 时间线过滤：错误、重试、工具升级、计划调整、模型调用、暂停/恢复。
- Node Detail Drawer：状态、版本、Attempt、输入/输出引用、耗时、Token 等技术统计。
- `/evidence/:id`：页面快照优先；无视觉快照时正文/原始内容降级。
- DOM locator 高亮/滚动定位；快照、正文、原始内容、Evidence 只读。
- 日志与时间线严格脱敏，不显示 API Key、Cookie、认证头、秘密表单值。

## 产出契约

- Quality Query API。
- Execution Timeline Query API。
- Plan DAG Query API。
- Evidence Query/Signed Download API。

## 自动化验收

- 质量指标点击后准确落到对应 Record 集合。
- 时间线能串起 task/run/node/attempt/trace。
- Evidence Viewer 查看的是当时 snapshot，不重新实时抓页面冒充证据。
- 无权限 evidence id 不泄漏元数据。

> **2026-08-21 实施证据（Execution Timeline 实时时间线流）**：时间线在既有 REST `/execution/timeline` 基础上新增 owner-scoped SSE 流 `GET /tasks/{task_id}/execution/timeline/stream`，REST 与流共用同一 `TimelineMapper` 输出富 `TimelineEvent`；前端执行页接入实时追加 + 阶段/DAG live 着色 + reconnect reconcile。相关决策见 D-078（状态：待讨论）。本地门禁证据见 `docs/audits/agent-execution-timeline-verification-2026-08-21.md`；真实任务验收仍 PENDING（需部署运行新端点的栈）。

## 完成门禁

- 对一条真实 Staging Record 能从 Data → Record Drawer → Quick Evidence → Full Evidence Viewer 完整追溯。

## 与后续联动

- M-15 导出质量报告/Artifact 元数据；M-17 使用同一追踪链做运维诊断。

### 2026-08-21 Execution Timeline 实时时间线流实施证据（本地）

- 实现：`backend/app/execution/timeline.py` 抽取共享 `TimelineMapper`（REST 与流一致映射）；`backend/app/execution/timeline_stream.py` 新增 SSE 流（replay 冻结 `replay_through_id` → 2s 轮询活区 → keepalive）；`backend/app/api/routes/execution.py` 挂载 `GET /tasks/{task_id}/execution/timeline/stream`（owner-safe，`?after_id` / `Last-Event-ID` 游标复用 `app/api/sse_cursor.py`）。前端：`execution.api.ts` + `useExecution.ts` 流客户端（单调去重 + 节流 snapshot + reconnect reconcile），`TimelineStepRow.vue` / `TaskExecutionView.vue` 实时步骤行与阶段/DAG live 着色。
- 零 migration：`alembic heads` 唯一 `0017`；不修改 Workflow/Temporal/Agent Loop/Provider/Extraction；既有 task SSE `/api/events/tasks/{task_id}` 不变（游标解析共享但逻辑逐字一致）。
- 测试证据（2026-08-21 本地全绿）：`tests/execution`（含 `test_timeline_mapper.py` 206 行、`test_timeline_stream_api.py` 462 行）+ `tests/api/test_task_events.py` + `test_understand.py` + `test_plan_api.py` 共 **143 passed**；`ruff check` 对本次涉及文件 **All checks passed**（`tests/ops/test_release_contract.py` 6 条 E501/F401 为 2026-08-12 `e357bec` 既有基线，本分支零改动）；`mypy app` **Success, no issues in 237 source files**；`create_app()` 正常。前端 `npm run test:unit` **41 files / 201 tests passed**、`type-check`、`lint:check`（0 errors）、`build` 全 PASS；`format:check` 仓库级失败为 Windows CRLF 既有基线（119 个未触碰文件），本分支变更文件单独 Prettier 检查通过。
- 真实任务验收：**PENDING**（需部署运行新端点的栈；本地 API 未运行，staging 后端尚未部署该端点）。验收脚本 `infra/scripts/_execution_timeline_staging_acceptance.py` 已按 `_m16/_m17` 模式创建（`_` 前缀，**未入库**），待部署后执行。证据完整记录于 `docs/audits/agent-execution-timeline-verification-2026-08-21.md`。

---

# M-15：CSV Artifact、完成总结、删除/恢复与对象生命周期闭环

## 目标

完成用户真正拿走数据、任务结束、删除/恢复和对象存储清理的产品闭环。

## 前置依赖

- M-12 结果/完成判定。
- M-13 数据查询/筛选。
- M-14 Evidence 引用。

## 必须完成

- 正式 CSV 默认只包含 PASSED。
- 待复核 CSV、审核完整 CSV；明确状态/原因字段。
- 导出可选全部当前分区或当前筛选结果。
- Artifact identity 包含 dataset version/filter snapshot/export type/content hash；相同导出复用已有 Artifact。
- Chat 正常完成/部分完成总结卡，展示停止原因、未覆盖范围、失败项，不能用虚假百分比。
- Task 删除：非运行任务进入 deleted；运行任务必须先 cancel。
- `/tasks?view=deleted` 支持恢复/永久删除。
- 永久删除执行 owner 校验和 PostgreSQL/Object Storage 级联清理。
- 生命周期清理 job：重型 HTML/正文/截图/浏览器快照/诊断包按部署保留期处理，但先做 Evidence 引用保护。
- FieldEvidence 必要最小原文片段长期保留策略落地。

## 产出契约

- `ArtifactService`。
- `ExportRequest/ArtifactRef`。
- `DeletionService`。
- `RetentionPolicy/CleanupResult`。

## 自动化验收

- 相同导出重复请求得到同一内容身份。
- 正式 CSV 不包含 NEEDS_REVIEW/REJECTED。
- 恢复 deleted Task 后数据仍可访问。
- 生命周期任务不会删除仍被 Evidence 引用对象。
- 永久删除用户 A 任务不会影响用户 B 相同 URL/hash 的逻辑资源引用；共享物理对象时必须通过引用计数/安全策略避免误删。

## 完成门禁

- 从 Chat 完成卡 → 数据 → 质量 → 导出 CSV → 下载全链路 Staging 可用。

## 与后续联动

- M-16 以本模块已经稳定的 Artifact、删除和生命周期语义作为可靠性压测对象。
- M-17 将本模块备份/生命周期要求纳入生产存储与恢复 Runbook。
- M-18 在 Production Smoke Test 中验证 CSV 下载、测试任务永久删除和对象清理。

---

# DEPLOY-GATE-4：Release Candidate Staging（M-13～M-15 后强制执行）

## 目标

到此阶段产品功能应已经形成用户可用闭环，服务器 Staging 必须按“候选正式版”进行验收。

## RC 验收场景

- 新用户注册登录。
- 配置模型与 Search Provider。
- 创建探索式任务和指定来源任务。
- Spec 确认、Workflow 执行、暂停/恢复、审批。
- 实时查看数据。
- 待复核人工/批量处理。
- 查看 Quality/Execution/Evidence。
- 正常完成与部分完成。
- 导出正式 CSV。
- 删除/恢复。
- 多用户隔离。

## 服务器要求

- staging 数据每日备份至少一次并可恢复验证。
- 保留最近稳定 image tag。
- migration 升级和回退 runbook 完成。
- 关键 Smoke Test 自动化脚本形成 `infra/scripts/smoke-*`。

## 门禁

- 任何 P0/P1 业务链错误不得进入 M-16 的生产发布准备。

---

# M-16：错误分类、自我纠错、资源池、并发与限流可靠性

## 目标

把 D-013、D-071 从“有逻辑”提升到“在多任务服务器环境下稳定运行”。

## 前置依赖

- M-07 Workflow。
- M-09～M-12 Activities。

## 必须完成

- 错误 taxonomy：网络超时、临时服务异常、Provider 限流、认证失败、额度/配额问题、结构变化、提取失败、质量失败、域名持续失败、资源等待。
- 指数退避 + jitter；只有输入/工具/参数/环境发生有效变化才允许纠错重试。
- URL/节点/域名/任务级重试和运行限制。
- 域名熔断器与恢复条件。
- Temporal Task Queue/Worker Pool：HTTP、Scrapy、Browser、LLM/Search；具体并发来自部署配置。
- 全局活跃任务限制、单用户限制、Provider rate limit。
- 资源不足显示 `WAITING_RESOURCE` 或等价状态，不误报失败。
- 浏览器 Worker 回收、超时、孤儿进程清理。
- 压力测试与容量基线文档。

## 产出契约

- `ErrorClass/RetryDecision`。
- `CircuitBreakerState`。
- `ResourceClass/QueuePolicy`。
- `CapacityConfig`。

## 自动化验收

- 限流模拟不会形成重试风暴。
- Browser 并发达到上限时新节点等待而不是继续开进程。
- 单用户大量任务不会占满全部资源。
- 熔断域名停止请求并保留人工处理建议。
- Worker 被 kill 后资源槽最终回收。

## 完成门禁

- 服务器 Staging 压测达到预设容量基线，CPU/内存/DB/Browser 无失控增长。

## 与后续联动

- M-17 根据实际容量和错误指标建立生产监控；M-18 生产发布按本资源配置启动 Worker。

---

# M-17：生产安全、可观测性、备份恢复与运维 Runbook

## 目标

满足 D-019～D-024 的正式服务器上线门禁，让系统可监控、可备份、可恢复、可回滚。

## 前置依赖

- M-01～M-16 全部完成。

## 必须完成

- Production 与 Staging 配置/数据库/对象 bucket/Temporal namespace 或等价边界分离。
- HTTPS、可信代理、安全 Cookie、CSRF/CORS、登录限流、密码策略、秘密主密钥管理。
- API/Worker 网络出口规则；数据库、Temporal、MinIO 不直接暴露公网。
- OpenTelemetry 覆盖 API → Workflow → Activity → Provider/Fetch；统一 trace id。
- 指标：API 错误率/延迟、Workflow backlog、Activity failure/retry、Worker resource、DB connection、Browser pool、Provider rate limit、对象存储错误。
- 日志脱敏扫描与结构化日志。
- PostgreSQL 自动备份；对象存储备份/复制策略；配置/密钥恢复文档。
- 至少执行一次真实 Restore Drill：新实例恢复 DB + 必要对象 → 登录 → 查询历史 Task/Evidence/Artifact。
- 生命周期清理任务在 production dry-run 后再启用真实删除。
- 运维 Runbook：服务起停、扩容 Worker、Provider 故障、数据库迁移失败、Temporal 故障、对象存储故障、磁盘告警、证书更新、回滚。
- CI/CD 生产发布权限与人工确认门禁。

## 产出契约

- production compose/deploy config。
- backup/restore scripts。
- smoke/health scripts。
- dashboards/alerts 基础配置。
- `docs/runbooks/*`。

## 自动化验收

- 关闭 API/Worker/DB/Temporal/Object Storage 的故障演练能产生可诊断信号。
- Restore Drill 有记录且恢复数据一致。
- secrets/log scan 不出现明文凭据。
- 生产镜像只包含运行依赖，不携带开发私钥/本地 `.env`。

## 完成门禁

- Production readiness checklist 全部通过。
- 未通过 Restore Drill 不允许进入 M-18。

## 与后续联动

- M-18 只执行发布，不再临时补基础安全/备份能力。

---

# M-18：Production 首次正式发布、灰度验证与回滚闭环

## 目标

把经过 Staging 验证的版本正式上线，并确保“发布失败可以安全回去”。

## 前置依赖

- M-01～M-17 全部 DONE。
- DEPLOY-GATE-1～4 全部通过。

## 必须完成

- 生成不可变 release tag/image tag，并记录代码、迁移和镜像摘要。
- 发布前执行 Production 数据库与对象存储备份。
- 按“兼容 migration → API → Worker → Frontend”的顺序发布。
- 发布后执行 health/readiness、Production Smoke Test 和小规模真实任务灰度。
- 达到回滚条件时立即停止扩大流量并按既定 Runbook 回到上一稳定版本。
- 发布过程不得在服务器容器内手工热改代码或绕过 CI 产出不可追踪版本。

## 发布前冻结

- 生成 release tag 和不可变 image tag。
- 记录 migration version、frontend build、api/worker image digest。
- Production 数据库和对象存储执行发布前备份。
- 确认上一稳定版本镜像仍可拉取。

## 发布顺序

1. 部署向后兼容的数据库 migration。
2. 部署 API。
3. 部署 core/HTTP/LLM/Search worker。
4. 部署 Browser worker。
5. 部署 Frontend。
6. 执行 health/readiness。
7. 执行 Production Smoke Test。
8. 小规模真实任务灰度。
9. 观察关键指标和错误率。
10. 宣布 release 完成。

## Production Smoke Test

- 注册/登录测试账号。
- 配置测试 Provider。
- 跑一个小型指定来源任务。
- 验证 Spec → Plan → Workflow → Fetch → Extract → Validate → Data → Evidence → CSV。
- 暂停/恢复测试。
- 下载 CSV。
- 删除测试任务并永久清理测试数据。

## 回滚条件

出现以下任一情况立即停止扩大流量并回滚：

- 登录/认证大面积失败。
- Task 无法启动或 Workflow 大量失败。
- 数据跨用户泄漏风险。
- Migration 造成核心数据不可读。
- Worker 重试风暴或服务器资源失控。
- CSV/Evidence 明显错误或对象存储写入失败。

## 回滚动作

- 前端/API/Worker 回到上一稳定 image tag。
- migration 仅在已验证可逆时自动回滚；不可逆迁移必须使用前向修复策略，并在发布前就设计兼容窗口。
- 恢复后再次执行 Smoke Test。
- 记录 Incident、根因和后续修复模块，不在生产服务器直接热改代码。

## 自动化验收

- `infra/scripts/smoke-production` 或等价脚本必须覆盖登录、Provider 测试、最小 Task E2E、暂停/恢复、Evidence、CSV 下载和测试任务清理。
- health/readiness 脚本必须对 web/api/worker 依赖状态给出可机器判断结果。
- release manifest 校验脚本必须确认正在运行的 frontend/api/worker image tag 与预期 release 一致。
- 回滚演练至少在 Staging 使用同一部署脚本验证过；Production 只执行已验证的回滚步骤。

## 完成门禁

- Production Smoke Test 通过。
- 至少一条真实小任务完整成功。
- 关键指标观察期无 P0/P1 异常。
- 备份、回滚、版本记录齐全。
- M-18 状态设为 `DEPLOYED`。

## 与后续联动

- M-18 是第一版工程计划的终点；其稳定发布物成为后续迭代的生产基线。
- 上线后的缺陷和新需求必须以新的模块/版本计划进入 Git 与 Staging 验证，不直接修改本批已完成模块的历史验收结论。
- Production 运行数据、告警和 Incident 记录反向输入下一版本容量、安全和产品优化决策。

---

# DEPLOY-GATE-5：Production 上线验收（M-16～M-18）

## 目的

确认第一版已经不是“服务器上能打开页面”，而是具备可持续运行、可恢复、可回滚的正式生产能力。

## 强制验收

- M-16 的资源池、用户限制、重试/熔断在 Production 配置下生效。
- M-17 的 HTTPS、网络边界、日志脱敏、监控、备份和 Restore Drill 已通过。
- M-18 使用不可变 release/image tag 发布。
- Production Smoke Test 全部通过。
- 至少完成一条小规模真实指定来源任务，并能查看 Evidence、Quality 和 CSV。
- 回滚路径已验证，上一稳定 image tag 和兼容数据库状态可恢复。
- 上线版本、migration version、部署时间、备份点和 Smoke 结果全部记录。

## Gate 结论

- 全部 PASS：M-18 = `DEPLOYED`，第一版工程实施完成。
- 任一 P0/P1 条件 FAIL：发布失败，必须回滚并回到对应模块修复，不得把问题留给“上线后再处理”。


## I-004：部署从项目中期开始，服务器 Staging 是持续验收环境，不是最后一次性上线

- 状态：已确认
- 日期：2026-08-10
- 第一次服务器部署固定在 M-04 完成后，而不是等全部模块开发完。
- 部署检查点：
  - `DEPLOY-GATE-1`：M-01～M-04，基础架构/认证/Provider/状态机首次 Staging。
  - `DEPLOY-GATE-2`：M-05～M-08，真实 Agent Task/Spec/Plan/Temporal/Approval Staging。
  - `DEPLOY-GATE-3`：M-09～M-12，真实网页采集 E2E Staging。
  - `DEPLOY-GATE-4`：M-13～M-15，完整产品 Release Candidate Staging。
  - `DEPLOY-GATE-5`：M-16～M-18，生产可靠性完成后正式 Production。
- 每个 Gate 都必须执行 migration、health/readiness、Smoke Test、版本记录和回滚检查。
- Gate 失败时，后续模块默认阻塞，优先修复部署环境/兼容性问题；不得长期保持“本地已完成但服务器不可运行”的分叉状态。
- Staging 和 Production 即使暂时部署在同一台物理服务器，也必须使用独立域名/端口入口、数据库、对象存储 bucket、配置和秘密，禁止共享业务数据。

## I-005：第一版推荐 Docker Compose + 镜像发布，不引入 Kubernetes

- 状态：已确认
- 日期：2026-08-10
- 目标：满足服务器长期运行、可重复部署、可回滚和 Worker 多角色隔离，同时控制运维复杂度。
- 推荐发布方式：
  1. Git 主分支/Release Tag 触发 CI。
  2. CI 跑测试并构建 `web`、`api`、`worker` 不可变镜像。
  3. 镜像推送到受控 Container Registry。
  4. 服务器通过版本化 compose 文件拉取指定 tag。
  5. 执行 migration。
  6. `docker compose up -d` 滚动/分角色重启。
  7. 执行 Smoke Test。
- Worker 可以复用同一个 `worker` image，通过环境变量/启动命令选择 Task Queue，不维护多个分叉代码镜像。
- 第一版不使用 Kubernetes；当未来出现多服务器水平扩容、独立团队维护、复杂弹性调度等明确需求时，再评估迁移。

# Agent 每次领取模块时必须使用的任务模板

后续实现 Agent 在开始 M-XX 前，必须生成并维护如下模块工作记录：

```markdown
# M-XX 模块执行记录

状态：IN_PROGRESS
负责人/Agent：执行时必须填写当前 Agent/会话标识，不得留空。
基线 Commit：执行时必须记录开始该模块前的真实 Git commit SHA。
依赖模块：执行时必须填写本总纲依赖矩阵中的实际模块编号，并确认均为 DONE/DEPLOYED。
目标环境：从 local / staging / production 中填写本模块实际目标环境；部署 Gate 模块不得填写 local。

## 1. 本模块目标
逐字引用本总纲中当前 M-XX 的目标，不增加产品范围。

## 2. 输入契约
- 上游数据模型：列出当前模块真实读取的表/DTO/版本名。
- API/事件/Workflow 契约：列出当前模块真实调用的 endpoint、event schema、Workflow/Activity 类型名。
- 使用的已有页面/Drawer：列出当前模块真实接入的已确认页面/Overlay；不适用时写“无”。

## 3. 本模块实现清单
- [ ] 数据模型/迁移
- [ ] 领域服务
- [ ] API/Workflow/Activity
- [ ] 前端交互（适用时）
- [ ] 安全/用户隔离
- [ ] 幂等/错误路径
- [ ] 自动化测试
- [ ] 联动测试
- [ ] 文档

## 4. 明确不做
列出下一模块或以后才做的内容，防止范围漂移。

## 5. 验收命令与证据
每条验收必须写出真实可执行命令、预期条件和本次实际结果；禁止填写“见上文”或留空。至少包含：
- 单元/契约测试命令。
- 该模块关键集成测试命令。
- 若涉及前端，记录 E2E 测试命令。
- 若涉及 migration，记录 upgrade/rollback 检查命令。
- 若属于 Deploy Gate，记录服务器 smoke script 与真实 image tag。

## 6. 跨模块联动结果
- 上游兼容：PASS/FAIL
- 下游契约测试：PASS/FAIL

## 7. 部署结果（若属于 Gate）
- image tag：...
- migration version：...
- server environment：...
- smoke result：PASS/FAIL
- rollback verified：YES/NO

## 8. 完成结论
只有所有门禁 PASS 后，状态才能改为 DONE/DEPLOYED。
```

# 模块依赖矩阵

| 模块 | 核心依赖 | 核心产出 | 主要下游 |
|---|---|---|---|
| M-01 | 无 | 工程/Infra/CI | 全部 |
| M-02 | M-01 | Auth/ownership | 全部用户数据模块 |
| M-03 | M-01,02 | Model/Search/Credential | M-06,09,10,11 |
| M-04 | M-01~03 | Domain/State/Event/Idempotency | M-05~18 |
| M-05 | M-02,04 | Vue Shell/Routes/Overlay | M-06,13,14,15 |
| M-06 | M-03~05 | Task Draft/Chat/Spec/Template | M-07,08 |
| M-07 | M-04,06 | Temporal/SSE/Pause/Resume | M-08~16 |
| M-08 | M-04,06,07 | Plan/Node Registry/Approval | M-09~12 |
| M-09 | M-03,07,08 | Search/Robots/Frontier | M-10,12 |
| M-10 | M-03,09 | Fetch/Snapshot/Browser | M-11,14,15 |
| M-11 | M-06,10 | Extract/Evidence | M-12,14 |
| M-12 | M-04,11 | Validate/Review/Quality/Completion | M-13~15 |
| M-13 | M-05,07,12 | Data/Review UX | M-14,15 |
| M-14 | M-07,11,12,13 | Quality/Execution/Evidence UX | M-15,17 |
| M-15 | M-12~14 | Artifact/Delete/Retention | M-17,18 |
| M-16 | M-07,09~12 | Reliability/Resource Pools | M-17,18 |
| M-17 | M-01~16 | Prod Security/Backup/Observability | M-18 |
| M-18 | M-01~17 | Production Release | 上线运营 |

# Agent 实施优先级与停止规则

1. 严格按 M-01 → M-18 顺序推进；只有某模块内部明确可并行的测试/前后端小任务可以并行。
2. 每完成一个模块立即提交；不要积累 4～5 个模块后一次性大提交。
3. 每到 DEPLOY-GATE 必须先上服务器 Staging；服务器失败优先修复，不继续堆本地功能。
4. 任何模块发现需求缺失：
   - 若可从 D-001～I-005 确定性推导，则按既有原则实现并记录推导。
   - 若会改变用户体验、权限、安全、数据含义、状态机或部署架构，则停止该点并请求新的决策，不擅自决定。
5. 任何线上/服务器紧急问题不得直接手改容器内代码；必须回到 Git 修复、测试、构建新镜像、重新部署。
6. 第一版以“一个可稳定运行的服务器产品”为完成标准，而不是“代码仓库里的功能看起来齐全”。

# 当前工程实施阶段结论

- 需求与 UI/UX 决策已经足够进入开发实施。
- 第一版实施划分为 18 个闭环模块、5 个服务器部署检查点。
- 微服务思想通过部署角色和领域边界体现，但保持 Monorepo、统一领域模型和有限服务数量。
- 第一次服务器 Staging 在 M-04 后开始，之后每 3～4 个模块强制重新部署和验收。
- 只有 M-18 Production Release 完成并通过 Smoke Test、回滚与备份门禁，项目第一版才视为真正完成。

## 2026-08-16 Execution Readiness Incident 实施证据

- M-06/M-08：来源契约、Plan source invariant 与 `VALID` / Preflight `READY` 双门禁已实现并有定向测试。
- M-07/M-16：Workflow 启动统一经过持久化 preflight；NodeRun、NodeAttempt、Checkpoint、DomainEvent 与终态 CAS 已实现。
- M-09/M-10/M-11/M-12：冻结搜索配置、命名来源约束、真实 Artifact executor、失败优先与互斥完成语义已实现。
- M-14：owner-scoped Execution snapshot、timeline、规范 SSE、安全 payload 和 Task Chat 进度面板已实现；前端以 snapshot 为事实源，SSE 负责增量刷新与重连 reconcile。
- M-15：Artifact executor 真实写入对象存储，并把存储失败映射为 typed runtime failure。
- M-17：低基数执行指标与关键 invariant 记录已接入；完整 Staging 可观测性验收仍待发布门禁。
- M-18：**未完成**。当前状态为 `CODE_COMPLETE`，不得标记 Staging/Production 已验证。PR、CI、不可变 GHCR digest、release manifest、备份、迁移、浏览器、Temporal/数据库一致性和回滚证据必须在实际发布时补齐。
- 用户于 2026-08-16 指示简化后续门禁：停止反复扩展边界审查，只保留事故主链定向测试、类型/静态检查与一次核心烟雾验收；不得因此降低发布证据的真实性要求。
