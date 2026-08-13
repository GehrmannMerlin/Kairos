# M-05 模块执行记录

状态：IN_PROGRESS → DONE（local）
负责人/Agent：Claude Code — 2026-08-10
Baseline（DEPLOY-GATE-1 PASS）SHA：`bd8440f0d4fc59695c0ce5f3f6523212955d3d8e`
依赖模块：M-01（DONE）、M-02（DONE）、M-03（DONE）、M-04（DEPLOYED）、DEPLOY-GATE-1（PASS）
目标环境：local（M-05 不属于 Deploy Gate；下一次服务器 Gate 为 DEPLOY-GATE-2，必须等 M-05～M-08）

> 说明：M-05 基于 DEPLOY-GATE-1 已 PASS 的 `bd8440f` 开发。分支 `feature/M-05-app-shell-navigation` 未 push / 未 merge。服务器 Gate-1 环境保持稳定，未做任何服务器变更。

## 1. 本模块目标

把 D-031～D-067 已确认 UI/UX 架构落成可导航、可鉴权、可接真实 API、无假业务数据的前端框架：
- 全局 App Shell（可折叠 Sidebar + UserMenu）、13 类路由完整注册。
- Task 顶部只保留「对话 / 数据 / 质量」。
- Overlay Drawer（7 类）与 Modal/Sheet（5 类）基础设施。
- 工作台 / 我的任务 / 模板 / 模型配置 / 设置与已存在后端真实联通；未实现业务为明确 Empty State。
- 统一 Deep Link 解析、全局 API Error Mapper、`allowed_actions` 消费机制。
- 极小只读 owner-safe Task Shell Query API（`GET /api/tasks`、`GET /api/tasks/{id}`）。

## 2. 输入契约

- 后端复用：M-02 `require_user` / `assert_owned` / `NotFoundError`、M-04 `TaskRepository.get_owned/list_by_user`、`app.state.states.allowed_task_actions`。
- 前端复用：M-01 ApiClient/ApiError/useAsync、M-02 `authStore`（CurrentUserStore）、M-03 `providers.api` + `ModelConfigDrawer/SearchConfigDrawer`。
- 未新增第三方运行时依赖；新增 devDependency `@playwright/test`（本仓库无 Cypress/Playwright，按规范选 Playwright）。

## 3. 本模块实现清单

- [x] 后端 Task Shell Query API：`GET /api/tasks`、`GET /api/tasks/{task_id}`（owner-safe 404，只读，无 Command）
- [x] App Shell：`AppLayout` + `AppShell`（Sidebar 收起 localStorage、UserMenu 显示 email/display_name、退出登录走真实 API）
- [x] 13 类路由完整注册 + 守卫 + 404 + `/` → `/app`；移除 M-01 健康检查落地页（HomeView）
- [x] 页面骨架：工作台 / 我的任务 / 模板 / 模板编辑 / 设置 / Task Chat / Data / Quality / Execution / Evidence（全部真实 Empty State）
- [x] CurrentUserStore 契约：`authStore` 扩展 `loading/error/loadCurrentUser`
- [x] 全局 API Error Mapper：`ApiError.code` + `apiErrorMapper`（unauthenticated / model_not_configured / search_provider_not_configured / not_found / conflict / rate_limited / service_unavailable / network）+ 通知层
- [x] Task Shell：`useTaskShell`（owner-safe 加载）+ TaskShellStore + 顶部仅「对话/数据/质量」Tab + 状态按钮开 Task Status Drawer
- [x] 我的任务 / 工作台最近任务：真实读取 `GET /api/tasks`
- [x] Overlay 基础设施：`DrawerStore/DrawerHost`（7 类型）+ `ModalStore/ModalHost/SheetHost`（5 类型）；`MODEL_REQUIRED` 可用最小实现引导 `/models`
- [x] Provider 编辑复用 M-03 Drawer（不重写、不建第二套）
- [x] 设置四区：账户 / 安全（改密、会话、退出其他设备、撤销、退出登录）/ 采集默认值（真实默认模型）/ 存储与数据（后续接入占位）
- [x] Deep Link：`parseTaskQuery`（approval / status / review_type / source_type，路由不丢 query）
- [x] allowed_actions：`can()` + `AllowedActionGate`（按钮显隐唯一来自后端）
- [x] Task Status Drawer：Task ID / state / version / spec+plan 版本 / 后端 allowed_actions；无假计数，未实现命令不作可点击假按钮
- [x] 测试：52 个 Vitest（路由守卫 parameterized、guestOnly、Sidebar 收起、TaskShell owner-safe 不泄漏、allowed_actions、deep link、error mapper 表驱动）+ 3 个 Playwright 导航 E2E

## 4. 明确不做

M-06（Task Draft / ChatMessage / Pydantic AI / CollectionSpec / Template CRUD / Spec confirm）、M-07（SSE 业务流 / pause-resume-cancel Workflow）、M-08（Plan / Approval 后端）、M-09～M-12（采集 / 提取 / 验证）、M-13～M-15（Data/Quality/Evidence/Export 真实业务）、DEPLOY-GATE-2（必须等 M-05～M-08）、远程 Git 集成、服务器任何变更。

## 5. 验收命令与证据

| 验收项 | 命令 | 预期 | 实际结果 |
|---|---|---|---|
| 前端 lint | `npm run lint:check` | PASS | PASS |
| 前端 format | `npm run format:check` | PASS | PASS |
| 前端 type-check | `npm run type-check` | PASS | PASS |
| 前端 build | `npm run build` | PASS | PASS |
| 前端 scoped Vitest | `npm run test:unit` | 全 PASS | 10 files / 52 tests PASS |
| 导航 E2E | `npm run test:e2e` | 3 scenarios PASS | PASS |
| 后端 task shell pytest | `pytest tests/api/test_task_shell.py` | 4 passed | PASS |
| 后端 ruff | `ruff check app tests && ruff format --check` | PASS | PASS |
| 后端 mypy | `mypy app` | PASS | PASS（67 files） |
| secret scan | `git grep` 真实 Key 模式 | 无泄漏 | PASS |
| fake data scan | grep mock/假数据/静态计数 | 仅 Empty State | PASS |

## 6. 跨模块联动结果

- 上游兼容：PASS — M-02 Auth（login/register/session/logout 真实 API）、M-03 Models（列表/新增/编辑/换 Key/测试/设默认/删除 保留，Provider 编辑复用原 Drawer）、M-04（state/allowed_actions/owner 全来自后端，前端无状态机复制）。
- 下游契约：PASS — Task Shell Query API 为 M-06+ 提供 owner-safe Task 读取契约；App Shell / Drawer / Modal / Deep Link / allowed_actions / Error Mapper 为 M-06～M-15 提供可复用基础设施。

## 7. Git 证据

- 分支：`feature/M-05-app-shell-navigation`（未 push）
- Commits（8 个，各自可独立验证）：
  - `d4f7ea6` feat(api): add owner-safe task shell query endpoints
  - `fb83548` feat(web): add authenticated app shell and router map
  - `3f2648d` feat(web): add global api error mapper and current user store
  - `ae77332` feat(web): add task shell and primary navigation
  - `5ace696` feat(web): add overlay drawer and modal infrastructure
  - `793daf2` feat(web): integrate settings and provider pages
  - `a7a7f80` feat(web): add deep links and allowed action gates
  - `af7e2b9` test(web): add focused navigation coverage
  - `e6eecca` chore(web): normalize formatting in shell and task files
  - （+ 计划文件 `docs/superpowers/plans/2026-08-10-m05-vue-app-shell-navigation.md`）

## 8. 完成结论

- M-05 全部 scoped 门禁 PASS；无假业务数据；M-02/M-03/M-04 兼容；13 类路由完整；Overlay / Deep Link / allowed_actions / Error Mapper 契约建立；基础导航 E2E PASS。M-05 local = DONE。
- 服务器 Gate-1 环境未动；DEPLOY-GATE-2 留待 M-05～M-08 完成后执行。
