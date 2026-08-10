# M-02 实现计划：注册登录、Session、安全边界与用户数据隔离

- 日期：2026-08-10
- 分支：`feature/M-02-auth-session-isolation`（基于已验证的 M-01 HEAD `4533eb7`，M-01 尚未合并入 main）
- 依赖模块：M-01（DONE）
- 权威文档依据：`agent-business-logic-log.md` D-020/D-022/D-023/D-052/D-053；`agent-project-implementation-plan.md` M-02；`agent-code-standards.md`；`agent-git-standards.md`

## Goal

在 M-01 工程骨架上完成邮箱+密码的注册/登录/登出、opaque 服务端可撤销 Session、统一 `CurrentUser` 依赖与所有权校验，并建立最小前端认证闭环（`/login`、`/register`、受保护 `/app`）。任何业务对象从第一天起都不能跨用户访问。

## Architecture

```text
web (Vue)  /login /register /app
  └─ /api/auth/*（经 vite 代理）
        └─ api/routes/auth.py（薄层：校验 DTO → 调 AuthService → 设/清 Cookie）
              └─ auth/service.py（业务：register/login/logout/change_password/session 生命周期）
                    └─ auth/repository.py（User/Session 存取）
                          └─ storage 数据库（PostgreSQL / 测试 SQLite）
    认证原语：auth/password.py（pwdlib argon2）、auth/tokens.py（sha256 token hash）、
             auth/rate_limit.py（内存可替换限流）、auth/deps.py（require_user / assert_owned）
```

依赖方向遵循 M-01：Route 不写 SQL/密码逻辑；service 是唯一业务入口；repository 负责持久化；领域层不反向依赖 FastAPI。

## Tech Stack

- 后端：FastAPI + SQLAlchemy 2（同步 engine，沿用 M-01 `app/infra/db.py`）、Pydantic v2、Alembic。
- 密码：`pwdlib[argon2]`（`PasswordHash.recommended()`）。
- Session token：`secrets.token_urlsafe(32)`；数据库只存 `sha256(token)` hex。
- 限流：内存滑动窗口 `InMemoryLoginLimiter`（`LoginRateLimiter` Protocol，可替换为 Redis 实现，本轮不引入 Redis）。
- 前端：Vue 3 Composition API + vue-router；沿用 M-01 `ApiClient` / `useAsync`。

## Global Constraints

- 不引入 RBAC、管理员、邀请系统、OAuth、邮箱验证、忘记密码、JWT、独立 Auth 服务、Redis。
- 所有用户仅 `user` 角色（D-022）；数据完全私有隔离（D-023）。
- 密码/`confirm_password` 不持久化；Session token 不写日志、不回传明文 token 给前端（Cookie 除外）。
- 跨用户访问返回 404（不泄漏资源存在性），认证缺失返回 401，限流返回 429。
- 全部 Schema 变更走 Alembic migration；关键唯一约束（email、token_hash）有数据库兜底。
- 测试遵守 A-Lite：只对高价值路径写测试，不追求覆盖率。

## Settings 新增（`backend/app/config.py`）

```python
session_cookie_name: str = "kairos_session"
session_cookie_httponly: bool = True
session_cookie_samesite: str = "lax"          # 校验 ∈ {"lax","strict","none"}
session_cookie_secure: bool = False           # dev；staging/prod 必须 True
session_cookie_path: str = "/"
session_cookie_max_age_seconds: int = 604800  # 7 天
auth_login_max_attempts: int = 5
auth_login_window_seconds: int = 900          # 15 分钟
```

环境变量入口：`KAIROS_SESSION_COOKIE_*`、`KAIROS_AUTH_LOGIN_*`。同步更新 `.env.example`（dev: `KAIROS_SESSION_COOKIE_SECURE=false`）。

## Interfaces

### 错误 DTO（统一，`app/api/errors.py` 注册 handler）

```json
{ "detail": { "code": "AUTH_REQUIRED", "message": "..." } }
```

稳定 code：`AUTH_REQUIRED`(401)、`INVALID_CREDENTIALS`(401，登录失败统一)、`EMAIL_TAKEN`(409)、`VALIDATION`(422)、`RATE_LIMITED`(429)、`NOT_FOUND`(404，含跨用户 404)、`AUTH_CSRF`(无需，SameSite 已足够)。

### 后端 DTO（Pydantic，`backend/app/auth/schemas.py`）

| DTO | 字段 |
|---|---|
| `RegisterCommand` | email: str, password: str, confirm_password: str（边界校验二者相等） |
| `LoginCommand` | email: str, password: str |
| `ChangePasswordCommand` | current_password: str, new_password: str, confirm_password: str |
| `UserDto` | id: int, email: str, display_name: str\|None, created_at: datetime |
| `SessionDto` | id: int, created_at: datetime, expires_at: datetime, revoked_at: datetime\|None, is_current: bool |
| `AuthResponse` | user: UserDto, session: SessionDto |
| `SessionsResponse` | sessions: list[SessionDto] |

### API 端点（`backend/app/api/routes/auth.py`，挂载 `/api/auth`）

| 方法 | 路径 | 认证 | 请求/响应 |
|---|---|---|---|
| POST | `/api/auth/register` | 公开 | RegisterCommand → 201 AuthResponse + set Cookie |
| POST | `/api/auth/login` | 公开 | LoginCommand → 200 AuthResponse + set Cookie（限流） |
| POST | `/api/auth/logout` | 需登录 | → 204 + clear Cookie（撤销当前 session） |
| GET | `/api/auth/me` | 需登录 | → 200 UserDto |
| POST | `/api/auth/password` | 需登录 | ChangePasswordCommand → 200 AuthResponse（轮换当前 session + 撤销其他 session + 新 Cookie） |
| GET | `/api/auth/sessions` | 需登录 | → 200 SessionsResponse |
| POST | `/api/auth/sessions/logout-others` | 需登录 | → 204（撤销除当前外全部） |
| DELETE | `/api/auth/sessions/{session_id}` | 需登录 | → 204（他人 session 或不存在 → 404） |

### 后端依赖（`backend/app/auth/deps.py`）

```python
def require_user(request: Request, db: Session = Depends(get_db)) -> User          # 401 AUTH_REQUIRED
def assert_owned(owner_id: int, current_user_id: int) -> None                       # 非 owner → 404 NOT_FOUND
def current_session_optional(...) -> Session | None                                 # 内部用
```

### 前端（`frontend/src/features/auth/`）

- `auth.api.ts`：`register/login/logout/me/changePassword/listSessions/logoutOthers`，类型 `AuthResponseDto`、`UserDto`、`SessionDto`、`ApiErrorBody{code,message}`。
- `useAuth.ts`：`state {status: loading|authenticated|guest, user: UserDto|null}`；`init()/login()/register()/logout()/setUser()`。
- 路由：`/login`、`/register` 公开；`/app` `meta.requiresAuth` 受保护；guard 依据 `useAuth` 状态重定向（未登录 → `/login`，已登录访问 `/login|/register` → `/app`）。
- 页面：`LoginView.vue`、`RegisterView.vue`（字段：邮箱/密码/确认密码）、`AppView.vue`（受保护占位：显示当前用户、登出按钮）。
- `App.vue` 改为直接渲染 `RouterView`；`AppShell` 移入受保护区域使用（保持最小，不抢 M-05）。
- `ApiClient.toApiError` 增加对 `{detail:{code,message}}` 的提取。

## Tasks

### Task 1：User & Session 持久化 + Migration

真实文件：
- 新增 `backend/app/auth/__init__.py`
- 新增 `backend/app/auth/models.py`：`User`（id, email unique, password_hash, display_name nullable, created_at, updated_at）、`Session`（id, user_id FK→users CASCADE, token_hash unique, user_agent nullable, created_at, expires_at, revoked_at nullable）
- 新增 `backend/app/auth/repository.py`：`UserRepository`（`create/ get_by_email/ get_by_id`）、`SessionRepository`（`create/ get_by_token_hash/ list_by_user/ revoke/ revoke_all_except`）
- 新增 `backend/alembic/versions/0002_create_users_sessions.py`（upgrade 建两张表 + 索引；downgrade 可逆）
- 修改 `backend/alembic/env.py`：导入 `app.auth.models` 使模型注册进 metadata
- 修改 `backend/app/infra/deps.py`：注册 auth repository 构造（`get_user_repo/get_session_repo`）
- 测试 `backend/tests/auth/test_persistence.py`（SQLite）：User/Session create/query roundtrip、email 唯一约束、token_hash 唯一

验证：`pytest tests/auth/test_persistence.py`

### Task 2：密码、Token、限流原语 + Settings

真实文件：
- 新增 `backend/app/auth/password.py`：`hash_password/verify_password`（pwdlib argon2）
- 新增 `backend/app/auth/tokens.py`：`generate_session_token() -> str`、`hash_session_token(token) -> str`（sha256 hex）
- 新增 `backend/app/auth/rate_limit.py`：`LoginRateLimiter` Protocol + `InMemoryLoginLimiter`（线程安全滑动窗口；`is_blocked/record_failure/reset`）
- 修改 `backend/app/config.py`：新增 cookie/限流字段 + samesite 校验
- 修改 `backend/pyproject.toml`：dependencies 增加 `pwdlib[argon2]`
- 修改 `.env.example`：新增 `KAIROS_SESSION_COOKIE_*`、`KAIROS_AUTH_LOGIN_*`
- 测试 `backend/tests/auth/test_security_primitives.py`：hash/verify 往返、错误密码 false、token 不可反推（hash≠token）、限流窗口内阻塞/窗口后放行/重置

验证：`pytest tests/auth/test_security_primitives.py` + `ruff/mypy`

### Task 3：AuthService + 错误分类

真实文件：
- 新增 `backend/app/auth/errors.py`：`AuthError/AuthenticationRequiredError/InvalidCredentialsError/EmailTakenError/RateLimitedError/NotFoundError`（携带稳定 code）
- 新增 `backend/app/auth/service.py`：`AuthService`（`register/login/logout/authenticate_session/change_password/list_sessions/revoke_session/revoke_other_sessions`）
- 新增 `backend/app/api/errors.py`：FastAPI exception handlers（`AuthError→401`、`EmailTakenError→409`、`RateLimitedError→429`、`NotFoundError→404`，统一 `{detail:{code,message}}`）
- 修改 `backend/app/main.py`：注册 handlers + auth 依赖
- 测试 `backend/tests/auth/test_service.py`（SQLite）：register 建 User+Session、邮箱唯一冲突、login 成功/失败、change_password 后旧 session 失效策略、session 解析（无效/过期/撤销返回 None）

验证：`pytest tests/auth/test_service.py`

### Task 4：Auth API + CurrentUser + Ownership

真实文件：
- 新增 `backend/app/api/routes/auth.py`：实现 §Interfaces 全部端点（薄层）
- 新增 `backend/app/auth/deps.py`：`require_user/assert_owned`
- 修改 `backend/app/api/router.py`：挂载 auth router
- 修改 `backend/app/main.py`：注册错误 handler（如 Task 3 未完成则在此完成）
- 测试 `backend/tests/auth/test_api.py`（TestClient + SQLite + 假 cookie jar）：
  - 注册→201+Cookie、重复邮箱→409
  - 未登录访问 `/me`→401
  - 登录→200+Cookie；登出→204+Cookie 清除；登出后访问 `/me`→401
  - 撤销 session 后该 Cookie 访问→401
  - 用户 A 撤销/读取 B 的 session→404（跨用户隔离回归测试）
  - 修改密码→旧 cookie 失效（按策略）、重新登录成功
  - 登录限流→429（代表性边界）

验证：`pytest tests/auth/test_api.py`

### Task 5：前端认证闭环

真实文件：
- 新增 `frontend/src/features/auth/auth.api.ts`
- 新增 `frontend/src/features/auth/useAuth.ts`
- 新增 `frontend/src/features/auth/LoginView.vue`、`frontend/src/features/auth/RegisterView.vue`
- 新增 `frontend/src/features/app/AppView.vue`（受保护占位）
- 修改 `frontend/src/app/router/index.ts`：新增路由 + `meta.requiresAuth` + guard
- 修改 `frontend/src/App.vue`：`RouterView` 直接渲染
- 修改 `frontend/src/app/shell/AppShell.vue`：保持最小（受保护区使用）
- 修改 `frontend/src/app/api/client.ts`：`toApiError` 提取 `detail.message`
- 修改 `frontend/src/main.ts`：挂载前 `init()` 认证状态
- 测试 `frontend/src/features/auth/auth.flow.test.ts`（Vitest + 路由）：未登录访问 `/app`→重定向 `/login`；登录成功→`/app`；注册成功→`/app`

验证：`npm run test:unit`（auth 相关）+ `lint:check` + `type-check` + `build`

### Task 6：M-02 Auth Smoke + 文档 + 收尾

真实文件：
- 新增 `backend/tests/integration/test_auth_smoke.py`（integration mark，需本地服务）：A 注册→`/me` 成功；B 注册；A 尝试撤销 B session→404；A 修改密码→旧 cookie 失效；重新登录成功；登出→protected 401
- 新增 `docs/operations/auth-session.md`（Session 工作方式、Cookie 安全配置、local/prod Secure 差异、TTL、logout/revoke、password change 后行为、ownership 用法、本地验证命令）
- 新增 `docs/implementation/M-02-execution.md`（按实施计划模板，真实填写）
- 修改 `.env.example`（确认含 auth 配置项）

验证：`KAIROS_RUN_INTEGRATION=1 pytest -m integration`；`python scripts/run_smoke.py`（M-01 回归，确认未破坏）；`pytest tests/auth/`；前端 scoped checks；migration `alembic upgrade head`

## 测试方式（A-Lite）

- 后端高价值测试集中在 `tests/auth/`：注册/登录、session 生命周期、跨用户隔离（永久回归）、修改密码 session 策略、登录限流边界。
- 集成 Smoke 一条（§Task 6）。
- 前端只测路由 guard 与登录/注册成功跳转。
- 不跑全量 integration / Browser E2E / 压力测试；不追求覆盖率。

## 明确不做（scope 边界）

M-03 Provider/CredentialVault、M-04 Task/状态机/Outbox/Checkpoint、M-05 完整 App Shell/13 页面、Task Chat、Temporal TaskWorkflow、Agent/Pydantic AI、Search/Scrapy/Playwright、Evidence/CSV/Quality、RBAC/管理员/邀请/邮箱验证/忘记密码/OAuth、Redis/K8s、生产部署、DEPLOY-GATE-1。

## 提交策略（Conventional Commits，预计 5~6 个）

1. `feat(auth): add user and session persistence`（Task 1）
2. `feat(auth): add password hashing and session token primitives`（Task 2）
3. `feat(auth): add auth service and error taxonomy`（Task 3）
4. `feat(api): add protected auth endpoints with ownership guard`（Task 4）
5. `feat(web): add login and registration flow with route guard`（Task 5）
6. `docs(auth): document session security behavior`（Task 6）

每 Commit 附带 Co-Authored-By。只本地 Commit；不 push/merge/tag。

---

## Self-Review

### CHECK 1 业务决策一致性
- D-022：多账号、统一 user 角色、无 RBAC/管理员 → 计划 User 无 role 字段，仅 email+password。PASS
- D-023：完全私有隔离 → `assert_owned` 404 统一，Session 撤销按 owner 校验，跨用户不泄漏。PASS
- D-052：设置页账户/安全能力 → 提供 me/password/sessions/logout-others 后端，不建完整 Settings UI。PASS
- D-053：邮箱+密码、/login /register、注册字段、无邮箱验证/忘记密码/OAuth、成功→/app、未登录→/login。PASS
- 无重新实现 D-018 单用户模式等作废决定。PASS

### CHECK 2 与 M-01 冲突
- Settings/health/db/temporal/minio/otel/compose/代理/端口不修改（仅 Settings 追加字段、deps 追加 auth 仓库构造、router 追加 auth、main 追加 handler）。PASS

### CHECK 3 模块边界
- 扫描 Tasks：无 M-03 Provider/CredentialVault、无 M-04 状态机/Outbox、无 M-05 完整 Shell/页面。仅 `AppShell` 结构微调以支撑受保护区（M-02 所需最小改动）。PASS

### CHECK 4 代码规范
- typed boundary（Pydantic DTO + typed service）、Route 薄层、migration、密码不泄漏（argon2 hash）、Session token 只存 sha256、ownership 后端强制、错误分类 auth/permission/rate_limit、无 Secret。PASS

### CHECK 5 测试复杂度
- 高价值测试：注册/登录、session 生命周期、跨用户隔离（永久回归）、改密策略、限流边界、一条集成 Smoke、前端 guard。无全量 integration、无 Browser E2E、无压力测试。PASS

### CHECK 6 Git
- 6 个可独立验证 Commit、Conventional Commits、不 push/merge/tag、工作树最终干净。PASS

### 附加检查
- placeholders：无 TODO/TBD/"以后补"/"适当错误处理"空话。PASS
- type/interface 一致性：DTO/端点/settings/service 方法名在全文一致。PASS

## PLAN SELF-APPROVAL: PASS

- business decisions: PASS
- M-01 compatibility: PASS
- module scope: PASS
- code standards: PASS
- A-Lite testing: PASS
- git standards: PASS
- placeholders: PASS
- type/interface consistency: PASS
