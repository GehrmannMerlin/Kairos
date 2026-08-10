# M-02 模块执行记录

状态：DONE
负责人/Agent：Claude Code — 2026-08-10
基线 Commit：`4533eb7`（M-01 HEAD，未合并入 main）
依赖模块：M-01（DONE）
目标环境：local

> 说明：M-02 基于未远程集成的 M-01 HEAD 开发，保持模块分支隔离，不重写 M-01 历史。分支 `feature/M-02-auth-session-isolation` 尚未 push/merge。

## 1. 本模块目标

> 完成 D-020～D-023、D-054～D-057 对注册登录和用户私有隔离的底层能力，使任何业务对象从第一天开始都不能跨用户访问。（摘自 agent-project-implementation-plan.md 的 M-02 章节）

## 2. 输入契约

- 上游数据模型：`users`、`sessions`（alembic 0002）。
- API/Workflow 契约（本模块产出）：
  - `POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`
  - `GET /api/auth/me`、`POST /api/auth/password`
  - `GET /api/auth/sessions`、`POST /api/auth/sessions/logout-others`、`DELETE /api/auth/sessions/{id}`
  - 错误 DTO：`{"detail": {"code": ..., "message": ...}}`
- 使用的已有页面/Drawer：`/login`、`/register` 新增；`/app` 受保护占位；`AppShell` 改为 slot（最小改动）。

## 3. 本模块实现清单

- [x] 数据模型/迁移：`User`（唯一邮箱）、`Session`（token sha256 摘要）、alembic 0002（可逆）
- [x] 领域服务：`AuthService`（register/login/logout/change_password/session 生命周期）+ repositories
- [x] API/Workflow/Activity：auth 路由（薄层）+ 统一错误 handler
- [x] 前端交互：登录/注册表单、受保护 `/app` 占位、路由守卫
- [x] 安全/用户隔离：`require_user`/`require_session`、`assert_owned`（跨用户 404）、密码 argon2、令牌只存 hash
- [x] 幂等/错误路径：登录限流（内存可替换）、统一认证失败文案、改密会话轮换策略
- [x] 自动化测试：后端 31 单测 + 3 集成；前端 8 单测
- [x] 联动测试：M-02 Auth Smoke（见 §6）
- [x] 文档：auth-session.md、local-dev/run 无破坏、本记录

## 4. 明确不做

M-03 Provider/CredentialVault、M-04 Task/状态机/Outbox/Checkpoint、M-05 完整 App Shell/13 页面、Task Chat、Temporal TaskWorkflow、Agent/Pydantic AI、Search/Scrapy/Playwright、Evidence/CSV/Quality、RBAC/管理员/邀请/邮箱验证/忘记密码/OAuth、Redis/K8s、生产部署、DEPLOY-GATE-1。

## 5. 验收命令与证据

| 验收项 | 命令 | 预期 | 实际结果 |
|---|---|---|---|
| 后端单测 | `pytest tests/` | 37 passed, 3 skipped | PASS（3 skipped = 集成，需 flag） |
| auth 单测 | `pytest tests/auth/` | 25 passed | PASS |
| ruff | `ruff check app tests && ruff format --check` | PASS | PASS |
| mypy | `mypy app` | PASS | PASS（33 files） |
| migration | `alembic upgrade head` | 0001→0002 | PASS（head=0002） |
| 集成 smoke | `KAIROS_RUN_INTEGRATION=1 pytest -m integration` | 3 passed | PASS（含 M-02 auth smoke） |
| 前端单测 | `npm run test:unit` | 8 passed | PASS |
| 前端 lint/format | `npm run lint:check && format:check` | PASS | PASS |
| 前端 type-check/build | `npm run type-check && build` | PASS | PASS |
| 容器回归 | `docker compose up -d --build api worker web` | 8 服务 up | PASS（api healthy，web→api 代理 200） |
| M-01 回归 | `python scripts/run_smoke.py` | SMOKE PASS | PASS（未破坏 M-01） |
| 容器级 auth 闭环 | register→me→logout→me | 201/200/204/401 | PASS |

## 6. 跨模块联动结果

- 上游兼容：PASS（M-01 health/smoke/集成无回归）。
- 下游契约测试：PASS — M-02 Auth Smoke（A 注册→/me；B 注册；A 撤销 B 会话→404；A 改密→旧会话失效；重登成功；登出→401）。

## 7. 部署结果

- 非 Deploy Gate；DEPLOY-GATE-1 待 M-01～M-04 后执行，本轮不执行。

## 8. 完成结论

- 全部门禁 PASS。M-02 = DONE。工作树最终干净，无 Secret 提交。
