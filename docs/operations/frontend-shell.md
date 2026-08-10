# 前端 Shell / 路由 / Overlay / 错误映射运维说明（M-05）

> 简要契约文档。完整计划见 `docs/superpowers/plans/2026-08-10-m05-vue-app-shell-navigation.md`。

## 1. 路由（D-048，13 类）

| 页面 | 路由 | 类型 |
|---|---|---|
| 登录 / 注册 | `/login`、`/register` | Public（guestOnly） |
| 工作台 | `/app` | Auth |
| 我的任务 | `/tasks` | Auth |
| 模板 | `/templates`、`/templates/new`、`/templates/:templateId/edit` | Auth |
| 模型配置 | `/models` | Auth |
| 设置 | `/settings` | Auth |
| Agent 对话 | `/tasks/:taskId/chat` | Auth+Task 一级 |
| 数据 | `/tasks/:taskId/data` | Auth+Task 一级 |
| 质量 | `/tasks/:taskId/quality` | Auth+Task 一级 |
| 执行详情 | `/tasks/:taskId/execution` | Auth+Task 二级（不成 Tab） |
| 证据查看器 | `/tasks/:taskId/evidence/:evidenceId` | Auth+Task 二级（不成 Tab） |

`/` → `/app`；404 为 Shell 内 Not Found。Task 主体默认进入 `/tasks/:taskId/chat`（D-050）。

## 2. 结构

- `src/app/shell/`：`AppLayout`（受保护布局）+ `AppShell`（Sidebar 收起 localStorage `kairos.sidebarCollapsed`、UserMenu、通知层、DrawerHost/ModalHost/SheetHost）。
- `src/app/router/`：路由表 + `deepLinks.ts`（`parseTaskQuery`：approval / status / review_type / source_type）。
- `src/app/error/`：`ApiError`（含 `code`）、`apiErrorMapper`（`mapApiError`）、`useAppNotice`（全局通知）。
- `src/app/overlay/`：`drawer.store` / `modal.store` + `DrawerHost`（7 类型）/ `ModalHost`（3 类型）/ `SheetHost`（2 类型）。均为 Overlay，不挤压布局。
- `src/app/actions/`：`can(action, allowed)` / `AllowedActionGate` —— 按钮显隐唯一来自后端 `allowed_actions`。
- `src/features/tasks/`：`tasks.api`（TaskShellDto）、`useTaskShell`（owner-safe 加载，404 → 通用 not-found 不泄漏）、`TaskShell`（三 Tab 一级工作区）。

## 3. 全局错误语义（`mapApiError`）

| 后端 code / status | kind | 处理建议 |
|---|---|---|
| AUTH_REQUIRED / INVALID_CREDENTIALS / 401 | `unauthenticated` | 引导登录 |
| MODEL_NOT_CONFIGURED（409） | `model_not_configured` | Model Required Modal → `/models` |
| SEARCH_PROVIDER_NOT_CONFIGURED（409） | `search_provider_not_configured` | 引导 `/models` 搜索区 |
| NOT_FOUND（404） | `not_found` | 通用 not-found，不泄漏资源存在性 |
| STALE_VERSION / IDEMPOTENCY_CONFLICT / ILLEGAL_TRANSITION / 409 | `conflict` | 明确冲突提示 |
| RATE_LIMITED（429） | `rate_limited` | 可恢复提示 |
| 5xx | `service_unavailable` | 稳定重试提示 |
| status 0 | `network` | 网络失败 |

## 4. 验证命令（M-05 scoped）

```bash
cd frontend
npm run lint:check && npm run format:check && npm run type-check && npm run build
npm run test:unit        # 52 tests（不跑 e2e/，见 vite.config exclude）
npm run test:e2e         # 3 Playwright 导航场景
cd ../backend
.venv/Scripts/python -m pytest tests/api/test_task_shell.py -q
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
```

## 5. 边界

- 不新增第 14 个一级产品页面；未实现业务用真实 Empty State。
- Task 状态 / allowed_actions 唯一来自后端；前端不复制状态机。
- Provider 编辑复用 M-03 Drawer；API Key 不可回读明文。
- DEPLOY-GATE-2 需要 M-05～M-08 完成后执行；M-05 本身不部署服务器。
