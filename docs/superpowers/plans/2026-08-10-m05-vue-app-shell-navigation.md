# M-05 Vue 全局 App Shell、13 类页面骨架与真实导航 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 D-031～D-067 已确认 UI/UX 架构落成「可导航、可鉴权、可接真实 API、无假业务数据」的 Vue 前端框架：全局 App Shell + 13 类路由 + Overlay Drawer/Modal 基础设施 + 统一 API Error Mapper + Deep Link + `allowed_actions` 消费机制。保护 M-02 Auth / M-03 Models 既有真实能力不被破坏，不提前实现 M-06+ 业务。

**Architecture:** 前端沿用当前「Vue 3 + vue-router + 模块级 ref store（无 Pinia）」既有风格，不引入 UI 框架、不重写 CSS、不更换状态方案。新增「带鉴权的嵌套路由布局（`AppLayout` → `AppShell`）」统一装载受保护页面；13 类路由完整注册。后端仅新增一个**极小只读 owner-safe Task Shell Query API**（`GET /api/tasks`、`GET /api/tasks/{task_id}`），复用 M-02 `require_user` / `assert_owned` / `NotFoundError`（跨用户 404 不泄漏）与 M-04 `TaskRepository` / `allowed_task_actions`。所有尚未实现的业务区域使用真实 Empty State，严禁硬编码假 Task/Record/Evidence/计数/百分比。

**Tech Stack:** Vue 3.5 + vue-router 4 + TypeScript strict + Vitest(jsdom) + ESLint + Prettier + vue-tsc + Vite（全部已有）；新增 devDependency `@playwright/test`（本仓库无 Cypress/Playwright，按规范选 Playwright）用于 1 个精简 Navigation E2E。后端 FastAPI + Pydantic + SQLAlchemy（复用 `app.infra.db.get_db` / `app.domain.repository.TaskRepository`）。

---

## Global Constraints

- **保护 M-02**：`/login`、`/register`、Session Cookie、`authStore`（CurrentUser）、真实 logout API、路由守卫全部保留；只扩展不重写。
- **保护 M-03**：`/models` 真实功能（列表/新增 Drawer/编辑 Drawer/换 Key/测试连接/设默认/删除）保留；Provider 编辑 Drawer 复用现有 `ModelConfigDrawer`/`SearchConfigDrawer`，**不创建第二套**；API Key 绝不可回读明文。
- **保护 M-04**：Task 的 state / allowed_actions / owner 全部来自后端 M-04 事实；前端**不复制状态机**，不写 `if (task.state === 'RUNNING')` 猜测。
- **13 类页面固定**：不新增 `/approvals /files /logs /plans /credentials /deleted /recycle-bin /models/:id` 等独立页面；未知能力优先 Drawer / Modal / Deep Link / 折叠区。
- **Task 顶部只有「对话 / 数据 / 质量」**：执行、证据是二级页面，不进 Sidebar、不成第 4 Tab。
- **Drawer/Modal 为 Overlay**：打开不挤压/不重排底层布局。
- **无假业务数据**：任何尚未实现的后端业务一律真实 Empty State + 明确「将在 M-06+/后续模块接入」文案；禁止 mock task/record/evidence/run/approval/count/百分比。
- **M-06 边界**：不实现 Task Draft 创建、ChatMessage 持久化、Pydantic AI、CollectionSpec、Template CRUD、Spec confirm。
- **M-07+ 边界**：不实现 SSE 业务流、pause/resume/cancel Workflow、Approval 真实后端、Data 真实查询、Quality 逻辑、Evidence 内容。
- **只跑 M-05 scoped 验证**：前端 lint/format/type-check/build + scoped Vitest + 1 个 Navigation E2E；后端仅新增 Task Shell API 时跑 ruff/mypy + 对应 scoped pytest（`tests/api/test_task_shell.py`）；禁全量 `pytest tests/`、禁服务器 Staging 部署、禁 DEPLOY-GATE-2。
- **不 push / 不 merge / 不 tag / 不 deploy**；本地 ~7 个 Commit；分支 `feature/M-05-app-shell-navigation`，基线 SHA = DEPLOY-GATE-1 HEAD `bd8440f0d4fc59695c0ce5f3f6523212955d3d8e`。
- 服务器 Gate-1 环境保持稳定，M-05 local = DONE 后停止，不进入 M-06。

---

## 术语与全局共享契约（跨 Task 一致）

**13 类路由**（D-048）：

| # | 页面 | 路由 | 类型 |
|---|---|---|---|
| 01 | 登录 | `/login` | Public |
| 02 | 注册 | `/register` | Public |
| 03 | 工作台 | `/app` | Auth |
| 04 | 我的任务 | `/tasks` | Auth |
| 05 | 模板列表 | `/templates` | Auth |
| 06 | 模板编辑 | `/templates/new`、`/templates/:templateId/edit` | Auth |
| 07 | 模型配置 | `/models` | Auth |
| 08 | 设置 | `/settings` | Auth |
| 09 | Agent 对话 | `/tasks/:taskId/chat` | Auth+Task |
| 10 | 数据 | `/tasks/:taskId/data` | Auth+Task |
| 11 | 质量 | `/tasks/:taskId/quality` | Auth+Task |
| 12 | 执行详情 | `/tasks/:taskId/execution` | Auth+Task 二级 |
| 13 | 证据查看器 | `/tasks/:taskId/evidence/:evidenceId` | Auth+Task 二级 |

**Task Shell Query DTO**（后端返回，M-04 事实）：

```json
{
  "task_id": 1, "title": "采集深圳供应商", "state": "DRAFT", "version": 1,
  "task_type": "directed", "current_spec_version": null, "current_plan_version": null,
  "allowed_actions": ["delete"], "created_at": "...", "updated_at": "..."
}
```

`GET /api/tasks` → `{"tasks": [TaskShellDto, ...]}`；`GET /api/tasks/{task_id}` → `TaskShellDto`；跨用户/不存在一律 404 `{"detail":{"code":"NOT_FOUND","message":"资源不存在"}}`；未认证 401 `AUTH_REQUIRED`。

**Drawer 类型**（D-067）：`TASK_STATUS | APPROVAL | CREDENTIAL | RECORD | EVIDENCE_QUICK | NODE_DETAIL | PROVIDER_EDIT`

**Modal/Sheet 类型**（D-067）：`COLLECTION_SPEC_EDITOR | TEMPLATE_VARIABLES | EXPORT | DELETE_CONFIRM | MODEL_REQUIRED`

**Global Error Kind**（`apiErrorMapper`）：`unauthenticated | model_not_configured | search_provider_not_configured | not_found | conflict | rate_limited | service_unavailable | network | unknown`

**allowed_actions 消费**：`can(action)` / `<AllowedActionGate action="...">` / `useAllowedActions(taskShell)` —— 按钮显隐/禁用唯一来自后端 `allowed_actions` 数组。

---

## Task 1: 后端极小只读 owner-safe Task Shell Query API + 测试

**Files:**
- Create: `backend/app/api/schemas.py`（TaskShellDto / TaskShellListDto）
- Create: `backend/app/api/routes/tasks.py`
- Modify: `backend/app/api/router.py`（挂载 tasks router）
- Create: `backend/tests/api/__init__.py`
- Create: `backend/tests/api/test_task_shell.py`

**Interfaces:**
- Consumes: `app.auth.deps.require_user`、`app.auth.models.User`、`app.infra.deps.get_db`、`app.domain.repository.TaskRepository`、`app.state.states.{TaskState, allowed_task_actions}`。
- Produces: `GET /api/tasks`（owner list）、`GET /api/tasks/{task_id}`（owner shell）。只读，无任何 Command。

- [ ] **Step 1: 写失败测试** `tests/api/test_task_shell.py`（沿用 `tests/auth/test_api.py` 的 TestClient+SQLite 模式）：注册 A/B；A 经 `TaskRepository`（同一 engine 会话）创建 Task；断言 A 可读自身 shell、`allowed_actions` 来自状态机（DRAFT→`["delete"]`）、B 读 A 的 Task 404、B 列表为空、未认证 401、未知 id 404。先运行确认失败（`ModuleNotFoundError: app.api.schemas` / 无 tasks router）。
- [ ] **Step 2: 实现 schemas + routes + 挂载。** `app/api/schemas.py` 定义 Pydantic DTO（字段见术语表）；`app/api/routes/tasks.py` 两个 GET handler，`TaskRepository(db).list_by_user(user.id)` / `get_owned(user.id, task_id)`，`allowed_actions=allowed_task_actions(TaskState(task.state))`；`router.py` include。
- [ ] **Step 3: 门禁 + Commit**
  ```bash
  cd backend && .venv/Scripts/python -m pytest tests/api/test_task_shell.py -v
  .venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
  .venv/Scripts/python -m mypy app
  cd .. && git add backend/app/api backend/tests/api
  git commit -m "feat(api): add owner-safe task shell query endpoints"
  ```
  Expected: PASS + Commit。

---

## Task 2: App Shell + Router Map + 页面骨架组件 + 导航

**Files:**
- Create: `frontend/src/app/shell/AppLayout.vue`（`<AppShell><RouterView/></AppShell>`）
- Rewrite: `frontend/src/app/shell/AppShell.vue`（Sidebar + Topbar/UserMenu + main + host 插槽）
- Create: `frontend/src/features/tasks/TasksView.vue`、`TaskChatView.vue`、`TaskDataView.vue`、`TaskQualityView.vue`、`TaskExecutionView.vue`、`TaskEvidenceView.vue`
- Create: `frontend/src/features/templates/TemplatesView.vue`、`TemplateEditView.vue`
- Create: `frontend/src/features/settings/SettingsView.vue`
- Rewrite: `frontend/src/features/app/AppView.vue`（工作台骨架）
- Modify: `frontend/src/app/router/index.ts`（13 类路由 + 嵌套 + 守卫）
- Modify: `frontend/src/features/home/NotFoundView.vue`（link → `/app`）；删除 `HomeView.vue`（M-01 健康检查落地页被工作台取代，`/` redirect `/app`）
- Modify: `frontend/src/features/providers/ModelsView.vue`（最小适配：去掉自有头部「← 返回工作台」，保留全部真实功能）
- Modify: `frontend/src/styles/base.css`（仅少量 shell 布局 token，如 sidebar 宽度变量）

**Interfaces:**
- Consumes: `authStore`（路由守卫）；不依赖 TaskShellStore（Task 4 接入）。
- Produces: 13 类路由全注册 + 页面骨架（真实 Empty State）。

- [ ] **Step 1: Router map。** 结构：
  ```
  /login /register                     → Public（guestOnly）
  / (AppLayout, requiresAuth) children:
     ''       → redirect /app
     app      工作台
     tasks    我的任务
     templates / templates/new / templates/:templateId/edit
     models   模型配置
     settings 设置
     tasks/:taskId (TaskShell) children: chat / data / quality / execution / evidence/:evidenceId
     :pathMatch(.*)* → NotFoundView（Shell 内）
  ```
  守卫逻辑沿用现有 `beforeEach`（`requiresAuth`/`guestOnly`）。`main.ts` 仍先 `await authStore.init()` 再 mount。
- [ ] **Step 2: AppShell**：左侧 Sidebar（手动收起，偏好存 `localStorage('kairos.sidebarCollapsed')`，收起只改 UI 布局，不清 store/不改 route/不刷新），导航项 = 工作台 `/app`、+ 新任务（M-05 指向 `/app` 的新任务区占位）、我的任务 `/tasks`、模板 `/templates`、模型配置 `/models`、设置 `/settings`；Topbar 右侧 UserMenu（显示 `user.email`，有 `display_name` 则显示；项：设置 `/settings`、退出登录 → `authStore.logout()` 真实 API + 跳 `/login`）；main 区 `<RouterView/>`；预留 `<DrawerHost/>`/`<ModalHost/>`/通知层位置（Task 5 挂入）。
- [ ] **Step 3: 页面骨架**（全部真实 Empty State，文案如「任务对话能力将在 M-06 接入」「暂无任务」「模板编辑将在 M-06 接入」「暂无执行记录」等）：AppView（新任务输入占位 + 最近任务空态）、TasksView、TemplatesView、TemplateEditView、SettingsView（四区静态骨架，Task 6 接真实）、TaskChatView/TaskDataView/TaskQualityView/TaskExecutionView/TaskEvidenceView。Task 子页面暂为静态骨架（Task 4 挂 TaskShellStore）。
- [ ] **Step 4: 门禁 + Commit**
  ```bash
  cd frontend && npm run lint:check && npm run format:check && npm run type-check && npm run build
  git add frontend/src/app frontend/src/features frontend/src/styles
  git commit -m "feat(web): add authenticated app shell and router map"
  ```
  Expected: 全 PASS + Commit。

---

## Task 3: CurrentUser 契约 + ApiError code + 全局 API Error Mapper

**Files:**
- Modify: `frontend/src/app/error/ApiError.ts`（增加 `code` 字段）
- Modify: `frontend/src/app/api/client.ts`（`toApiError` 解析 `detail.code`）
- Create: `frontend/src/app/error/apiErrorMapper.ts`（`ApiErrorKind` + `mapApiError(error) -> {kind,message,action}` + 表驱动）
- Create: `frontend/src/app/error/useAppNotice.ts`（轻量全局通知/错误层 store）
- Modify: `frontend/src/features/auth/useAuth.ts`（扩展 CurrentUserStore 契约：新增 `loading`、`error` ref，`loadCurrentUser` = init 别名；保留 `status/user/login/register/logout`）
- Create: `frontend/src/app/error/apiErrorMapper.test.ts`

**Interfaces:**
- Consumes: `ApiError`（status+code+detail）。
- Produces: 统一错误语义，组件不再手写 `if (status===401)` 复制判断。

- [ ] **Step 1: 失败测试** `apiErrorMapper.test.ts` 表驱动覆盖：`AUTH_REQUIRED/401`→`unauthenticated`；`MODEL_NOT_CONFIGURED/409`→`model_not_configured`（action→/models）；`SEARCH_PROVIDER_NOT_CONFIGURED/409`→`search_provider_not_configured`（action→/models searches）；`NOT_FOUND/404`→`not_found`；`STALE_VERSION|IDEMPOTENCY_CONFLICT|409`→`conflict`；`RATE_LIMITED/429`→`rate_limited`；`5xx`→`service_unavailable`；`status 0`→`network`。
- [ ] **Step 2: 实现** `ApiError` 增加只读 `code: string`（默认 `''`）；`toApiError` 若 `detail` 为 `{code,message}` 则提取两者；`apiErrorMapper.ts` 依据 `code` 优先、status 兜底映射 kind/message/可选 action；`useAppNotice.ts` 提供 `push(message, kind?)`/`clear` 供 AppShell 通知层消费。
- [ ] **Step 3: useAuth 扩展**：`status` 保持 `loading/authenticated/guest`；新增 `loading`（init 中 true→false）、`error: Ref<string|null>`（init/login/register 失败时记录，成功后清空）；新增 `loadCurrentUser = init`。路由守卫继续复用 `authStore.status`。
- [ ] **Step 4: 门禁 + Commit**
  ```bash
  cd frontend && npx vitest run src/app/error/apiErrorMapper.test.ts src/app/api/client.test.ts src/features/auth/auth.flow.test.ts
  npm run lint:check && npm run type-check
  git add frontend/src/app frontend/src/features/auth/useAuth.ts
  git commit -m "feat(web): add global api error mapper and current user store"
  ```
  Expected: PASS + Commit。

---

## Task 4: Task Shell + TaskShellStore + 任务/工作台真实数据接入

**Files:**
- Create: `frontend/src/features/tasks/tasks.api.ts`（`TaskShellDto`/`TaskShellListDto` + `listTasks`/`getTask`）
- Create: `frontend/src/features/tasks/useTaskShell.ts`（TaskShellStore：`taskId/summary/state/allowed_actions/loading/error/load`；owner-safe 404 → `notFound` 状态）
- Create: `frontend/src/features/tasks/TaskShell.vue`（task 上下文头部 + status 触发按钮 + 顶部 Tab 仅「对话/数据/质量」+ 子 `<RouterView/>`；二级 execution/evidence 不从 Tab 进入）
- Modify: `frontend/src/features/tasks/TasksView.vue`（接 `listTasks` 真实列表；点击 Task → `/tasks/:id/chat`；空态「暂无任务」）
- Modify: `frontend/src/features/app/AppView.vue`（工作台最近任务接 `listTasks` 前 N；新任务输入区占位「任务创建能力将在下一模块接入」）
- Modify: `frontend/src/features/tasks/TaskChatView.vue`/`TaskDataView.vue`/`TaskQualityView.vue`/`TaskExecutionView.vue`/`TaskEvidenceView.vue`（挂 TaskShell 上下文；Chat/Data/Quality 使用 primary tab 布局；Execution/Evidence 二级；owner-safe 404 → 通用 not-found，不泄漏 task 名称/状态）

**Interfaces:**
- Consumes: `tasks.api`（Task 1 后端）、`apiErrorMapper`（Task 3）。
- Produces: 任务相关页面真实读后端，owner 隔离；无 Task 命令。

- [ ] **Step 1: tasks.api + TaskShellStore。** `useTaskShell(taskId)`：`load()` 调 `getTask`；成功置 `summary/state/allowed_actions`；`ApiError` kind=`not_found` → `notFound=true`（渲染通用「任务不存在或无权访问」，不展示任何任务 metadata）。
- [ ] **Step 2: TaskShell**：顶部 Tab 用 `<RouterLink>` 到 chat/data/quality 并高亮当前；不渲染 execution/evidence Tab。status 按钮 → 打开 `TASK_STATUS` Drawer（Task 6 实现 content）。
- [ ] **Step 3: 页面接入。** TasksView 真实列表（无则「暂无任务」）；AppView 最近任务 + 占位输入；Chat/Data/Quality 各自 Empty State（「任务对话能力将在 M-06 接入」「暂无数据」「暂无质量报告」）；Execution「暂无执行记录」；Evidence 无真实 id → 安全 Empty/Not Found，不 fetch 外部页面。
- [ ] **Step 4: 门禁 + Commit**
  ```bash
  cd frontend && npm run lint:check && npm run type-check && npm run build
  git add frontend/src/features/tasks frontend/src/features/app/AppView.vue
  git commit -m "feat(web): add task shell and primary navigation"
  ```
  Expected: PASS + Commit。

---

## Task 5: Overlay Drawer + Modal/Sheet 基础设施

**Files:**
- Create: `frontend/src/app/overlay/drawer.store.ts`（typed `DrawerState` + `open(type,payload)/close/toggle`，模块级 ref store）
- Create: `frontend/src/app/overlay/DrawerHost.vue`（Overlay 定位，backdrop 点击/Escape 关闭，打开不重排布局）
- Create: `frontend/src/app/overlay/drawers/`：`TaskStatusDrawer.vue`（Task 6 填内容，本 Task 先注册路由）、`ApprovalDrawer.vue`（占位「该审批当前不可用」）、`CredentialDrawer.vue`、`RecordDrawer.vue`、`EvidenceQuickDrawer.vue`、`NodeDetailDrawer.vue`（各为安全契约占位）
- Create: `frontend/src/app/overlay/modal.store.ts` + `ModalHost.vue` + `SheetHost.vue`
- Create: `frontend/src/app/overlay/modals/`：`CollectionSpecEditorModal.vue`、`TemplateVariablesModal.vue`、`ExportModal.vue`、`DeleteConfirmModal.vue`（占位契约）、`ModelRequiredModal.vue`（最小可用：提示未配置模型 + 「去配置模型」→ `/models`；不实现 M-06 Draft 保留）
- Modify: `frontend/src/app/shell/AppShell.vue`（挂入 `<DrawerHost/>`、`<ModalHost/>`、通知层）
- Create: `frontend/src/app/overlay/drawer.store.test.ts`（open/close/typed payload）

**Interfaces:**
- Consumes: `drawer.store`/`modal.store`；`PROVIDER_EDIT` 描述符（Provider 编辑仍由 ModelsView 的 M-03 Drawer 承担，本基础设施只登记类型 + 占位，不创建第二套）。
- Produces: 可复用 Overlay 基础设施。

- [ ] **Step 1: 失败测试** drawer.store.test.ts：open 设置 type+payload、close 清空、同类型 payload 类型正确。
- [ ] **Step 2: 实现 store + hosts**：Drawer 用 `position: fixed; inset-inline-end: 0` Overlay，不 push/不 resize 内容；backdrop + Escape 关闭；Modal 居中遮罩；Sheet 底部/侧边 Overlay。均不引入第三方。
- [ ] **Step 3: 注册各 Drawer/Modal**：`DrawerHost` 按 type 映射组件；仅 `TASK_STATUS`（Task 6 实装）与占位组件；`ModalHost` 同理，`MODEL_REQUIRED` 为可用最小实现。
- [ ] **Step 4: 挂入 AppShell + 门禁 + Commit**
  ```bash
  cd frontend && npx vitest run src/app/overlay && npm run lint:check && npm run type-check && npm run build
  git add frontend/src/app/overlay frontend/src/app/shell/AppShell.vue
  git commit -m "feat(web): add overlay drawer and modal infrastructure"
  ```
  Expected: PASS + Commit。

---

## Task 6: Settings 真实接入 + 已有 Auth/Models 联动 + Deep Link + allowed_actions + Task Status Drawer

**Files:**
- Modify: `frontend/src/features/settings/SettingsView.vue`（四区真实/占位）
- Modify: `frontend/src/features/providers/ModelsView.vue`（已纳入 Shell；保留真实功能；错误提示走全局映射）
- Create: `frontend/src/app/router/deepLinks.ts`（typed query parser：`approval`、`status`、`review_type`、`source_type`；保留 query）
- Create: `frontend/src/app/actions/allowedActions.ts`（`can(action, allowed) -> boolean`）+ `AllowedActionGate.vue` + `useAllowedActions(store)`
- Create: `frontend/src/app/overlay/drawers/TaskStatusDrawer.vue`（实装基础内容）
- Create: `frontend/src/app/overlay/modals/ModelRequiredModal.vue`（实装可用最小版）
- Create: `frontend/src/app/actions/allowedActions.test.ts` + `frontend/src/app/router/deepLinks.test.ts`

**Interfaces:**
- Consumes: M-02 `auth.api`（changePassword/listSessions/logoutOthers/revokeSession/logout）、M-03 `providers.api`（default model 列表）、M-04 `TaskShellDto.allowed_actions`、`apiErrorMapper`。
- Produces: Settings 四区 + allowed_actions 统一消费 + Deep Link 稳定解析 + Task Status Drawer 基础。

- [ ] **Step 1: Settings 四区。** ① 账户资料：显示 email + display_name（若 M-02 无更新接口则只读展示）；② 安全：修改密码（真实 `changePassword`）、当前会话 + 退出其他设备（`listSessions`/`logoutOthers`/`revokeSession`）、退出登录（`logout`）、已保存网站凭据（占位「后续模块接入」，不展示任何凭据明文）；③ 采集默认值：默认模型真实来自 `listModelConfigs().is_default`（只读展示 + 引导 `/models`），字段扩展/高级运行默认值占位「后续接入」；④ 存储与数据：统计/清理/删除全部数据均占位「后续接入」。**禁止假开关。**
- [ ] **Step 2: Deep Link。** `deepLinks.ts` 导出 `parseTaskQuery(query) -> { approval?: string; status?: string; review_type?: string; source_type?: string }`；Router 不丢 query；页面用 helper，不手写十几份 `route.query as string`。Approval deep link 仅识别 + 表达 `APPROVAL` payload，无真实 API → Drawer 占位「该审批当前不可用」，不生成假审批。
- [ ] **Step 3: allowed_actions。** `can(action, allowed)` 纯函数 + `AllowedActionGate`（无 allowed → 隐藏/禁用）；Task Status Drawer 只展示后端当前允许动作；**command endpoint 未实现的 action 不作为可点击假按钮**，仅信息展示。
- [ ] **Step 4: TaskStatusDrawer 实装。** 展示 Task ID、state、allowed_actions、spec/plan version、metadata；不存在的计数显示「—」或不展示，禁写「已处理 120 / Passed 95」静态数字。
- [ ] **Step 5: 门禁 + Commit**
  ```bash
  cd frontend && npx vitest run src/app/actions src/app/router && npm run lint:check && npm run type-check && npm run build
  git add frontend/src/features/settings frontend/src/app
  git commit -m "feat(web): integrate settings and provider pages"
  git add frontend/src/app/actions frontend/src/app/router frontend/src/app/overlay/drawers
  git commit -m "feat(web): add deep links and allowed action gates"
  ```
  Expected: 2 个 Commit + PASS。

---

## Task 7: Focused Vitest + 精简 Navigation E2E（Playwright）

**Files:**
- Create: `frontend/src/app/router/router.guard.test.ts`（parameterized：未登录访问全部 Auth 路由 → `/login`；登录后访问 `/login`/`/register` → `/app`）
- Create: `frontend/src/features/tasks/taskShell.test.ts`（Tab 仅「对话/数据/质量」，无 Execution 第 4 Tab；allowed_actions 驱动，无本地状态猜测；无权限 task → 通用 not-found 不泄漏 metadata）
- Create: `frontend/src/app/shell/sidebar.test.ts`（收起 Sidebar：route 不变、store 不清空、user 不丢）
- Modify: `frontend/src/app/error/apiErrorMapper.test.ts`（Task 3 已有）补充分支
- Create: `frontend/playwright.config.ts` + `frontend/e2e/navigation.spec.ts`
- Modify: `frontend/package.json`（devDeps + `@playwright/test`；scripts `test:e2e`）
- Modify: `frontend/src/features/auth/auth.flow.test.ts`（保留）+ `frontend/src/features/providers/providers.test.ts`（保留）作为 M-02/M-03 回归

**Interfaces:**
- Consumes: 全前端产物。
- Produces: A-Lite scoped 测试 + 1 个基础导航 E2E（2～3 Scenario）。

- [ ] **Step 1: 安装 Playwright**
  ```bash
  cd frontend && npm i -D @playwright/test && npx playwright install chromium
  ```
  若浏览器下载失败，fallback：`playwright.config` 用 `channel: 'msedge'`（Windows 自带 Edge）。
- [ ] **Step 2: Vitest** 按上述文件表驱动/代表性测试；E2E 前全部单测过。
- [ ] **Step 3: Playwright E2E（page.route 拦截 `/api`，不依赖真实后端）**：
  - **Scenario A**：未登录 → `/app` → 重定向 `/login` → 填表登录（mock login 返回 user+session）→ `/app` 工作台可见。
  - **Scenario B**：登录后经 Sidebar 依次 `/app`→`/tasks`→`/templates`→`/models`→`/settings`，断言无 `pageerror`（无 JS fatal）。
  - **Scenario C**：mock owner-safe task fixture（`/api/tasks/1`）→ `/tasks/1/chat`→`data`→`quality`→`execution`；断言顶部只有「对话/数据/质量」；mock `/api/tasks/999` 404 → 通用 not-found，页面不含 task 名称。
- [ ] **Step 4: 门禁 + Commit**
  ```bash
  cd frontend && npm run lint:check && npm run format:check && npm run type-check && npm run build
  npm run test:unit && npm run test:e2e
  git add frontend/package.json frontend/package-lock.json frontend/playwright.config.ts frontend/e2e frontend/src
  git commit -m "test(web): add focused navigation coverage"
  ```
  Expected: 全 PASS + Commit。

---

## Task 8: 文档、Execution Record 与最终 scoped 验证

**Files:**
- Create: `docs/implementation/M-05-execution.md`（状态 IN_PROGRESS→DONE(local)、Baseline `bd8440f`、依赖 M-02/M-03/M-04/DEPLOY-GATE-1、13 routes/stores/drawers/modals/deep links/error mapping/tests/E2E/commits 真实记录）
- Create: `docs/operations/frontend-shell.md`（简短：路由表、AppShell/Overlay/错误映射契约、allowed_actions 消费、Deep Link 解析、scoped 验证命令）

**Interfaces:**
- Consumes: 全部 M-05 产物与验证结果。

- [ ] **Step 1: 完整 M-05 scoped verification**
  ```bash
  cd frontend && npm run lint:check && npm run format:check && npm run type-check && npm run build && npm run test:unit && npm run test:e2e
  cd ../backend && .venv/Scripts/python -m pytest tests/api/test_task_shell.py -v
  .venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
  .venv/Scripts/python -m mypy app
  ```
- [ ] **Step 2: Secret scan** `git grep -niE "sk-[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|ghp_"`（排除 docs/superpowers 历史）→ 无真实 Key。
- [ ] **Step 3: 文档 + Commit**
  ```bash
  git add docs/implementation/M-05-execution.md docs/operations/frontend-shell.md
  git commit -m "docs(web): record M-05 execution"
  ```
- [ ] **Step 4: 最终检查** `git status`（clean）、`git log --oneline`（~7 Commit）、确认不 push。

---

## Self-Review

**1. Spec coverage：**
- 13 类路由 → Task 2（Router map）。
- App Shell + 可折叠 Sidebar + UserMenu → Task 2。
- CurrentUserStore + API Error Mapper → Task 3。
- Task Shell + 顶部 3 Tab + TaskShellStore + owner-safe → Task 4。
- Drawer/Modal/Sheet 基础设施（7 Drawer 类型 + 5 Modal 类型）→ Task 5。
- Settings 四区真实/占位 + Models/Auth 联动 → Task 6。
- Deep Link + allowed_actions 消费 + Task Status Drawer → Task 6。
- 无假业务数据（所有 Empty State）→ Task 2/4/6 内置检查。
- 基础导航 E2E（3 Scenario）→ Task 7。
- M-02/M-03 回归 → Task 7 保留现有测试 + 回归断言。
- 后端 Task Shell Query API（owner-safe 404）→ Task 1。
- 保护既有 Auth/Models/State 不破坏 → Global Constraints + Task 2/4/6 最小适配原则。

**2. Placeholder scan：** 无 `TBD`；仅两类「占位」是有意的产品语义：① 尚未实现业务的页面 Empty State（「将在 M-06/后续模块接入」，符合 D-066/D-067）；② Drawer/Modal 类型契约占位（Approval/Credential/Record/Evidence/Node 明确「该审批当前不可用」等，不实现未来模块）。`HomeView.vue` 删除、`/` redirect `/app` 属 App Shell 统一，非占位。

**3. Type consistency：**
- `TaskShellDto` 字段在 Task 1（后端 Pydantic）与 Task 4（前端 `tasks.api.ts`）逐字段一致（task_id/title/state/version/task_type/current_spec_version/current_plan_version/allowed_actions/created_at/updated_at）。
- `DrawerType`/`ModalType` 枚举在 Task 5 定义，Task 6 各 Drawer/Modal 组件与 Host 映射一致。
- `ApiErrorKind` 在 Task 3 定义，Task 3/6 消费一致；`mapApiError` 返回 `{kind,message,action?}` 稳定。
- `can(action, allowed)` / `AllowedActionGate` 在 Task 6 定义，Task 6/7 测试一致。
- 路由 name 与导航/测试一致（`app/tasks/templates/models/settings/task-chat/task-data/task-quality/task-execution/task-evidence/not-found/login/register`）。
- `parseTaskQuery(query)` 返回值在 Task 6 定义、测试一致。

---

## 项目专项审批（M-05）

**CHECK 1 Business Decisions：** D-031（对话型主交互→TaskShell Chat 主工作区）PASS；D-032（Overlay Task Status Drawer→Task 5/6）PASS；D-044（顶部仅 3 Tab）PASS；D-045/046/049（工作台+我的任务+/tasks?view=needs_action）PASS；D-047/054（模板列表+独立全宽编辑页，M-06 填业务）PASS；D-048（13 类页面固定，无第 14 个一级页）PASS；D-050（Task 主体→/chat）PASS；D-051（/models 单页+Drawer）PASS；D-052（Settings 四区）PASS；D-055/056（execution/evidence 二级页）PASS；D-057（Approval Drawer+Deep Link，不建页）PASS；D-058（Sidebar 可折叠=UI preference）PASS；D-059（Credential Drawer 契约）PASS；D-063/064（Node Detail/Evidence Drawer 契约）PASS；D-065（deleted 用 /tasks?view=deleted，不建回收站页）PASS；D-066（MODEL_NOT_CONFIGURED→Modal→/models）PASS；D-067（Overlay 边界/命令绑真实后端）PASS；D-036（无费用 UI）PASS。

**CHECK 2 M-02 Compatibility：** /login、/register、Session Cookie、authStore、logout 真实 API、路由守卫全部保留/扩展；未破坏；无第二套 CurrentUser。

**CHECK 3 M-03 Compatibility：** /models 真实功能保留；Provider 编辑复用现有 Drawer，不重写；API Key 不回读。

**CHECK 4 M-04 Compatibility：** state/allowed_actions/owner 全来自后端；前端不复制状态机；无 `if(task.state==='RUNNING')` 猜测。

**CHECK 5 13 Routes：** 逐项注册；无第 14 个一级产品页面。

**CHECK 6 Overlay：** Drawer/Modal 均为 Overlay，不挤压/不重排布局。

**CHECK 7 No Fake Data：** 所有未实现业务真实 Empty State；无 mock business objects/count/百分比。

**CHECK 8 Deep Link：** approval/data 筛选/Task 路由稳定解析；无权限不泄漏。

**CHECK 9 A-Lite Tests：** 路由守卫 parameterized、allowed_actions、error mapper 表驱动、Task Tab、sidebar collapse、1 个 Navigation E2E；无 snapshot flood、无多浏览器矩阵、无全仓 backend。

**CHECK 10 M-06 Boundary：** 未实现 Task Draft/ChatMessage/Pydantic AI/CollectionSpec/Template CRUD/Spec confirm。

**CHECK 11 M-07+ Boundary：** 未实现 SSE 业务流/Pause/Cancel/Approval 后端/Data 查询/Quality/Evidence 内容。

**CHECK 12 Git：** ~7 Commit 各自可独立验证；不 push/merge/tag/deploy；服务器 Gate-1 环境不动。

---

PLAN SELF-APPROVAL: PASS

business decisions D-031~D-067: PASS
implementation plan M-05: PASS
M-02 compatibility: PASS
M-03 compatibility: PASS
M-04 compatibility: PASS
13-route boundary: PASS
task shell boundary: PASS
overlay infrastructure: PASS
deep-link contract: PASS
allowed-actions contract: PASS
no fake business data: PASS
M-06 boundary: PASS
M-07+ boundary: PASS
A-Lite testing: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS
