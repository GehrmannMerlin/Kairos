# Auth / Session 安全行为（M-02）

本文记录 M-02 认证与 Session 的确定行为，供后续模块和开发者复用。不是安全论文。

## 1. Session 工作方式

- 登录/注册成功后生成随机 `secrets.token_urlsafe(32)` 令牌。
- 浏览器只在 **HttpOnly Cookie** 里持有原始令牌。
- 数据库只保存 `sha256(令牌)` 的 hex 摘要（`sessions.token_hash`）。数据库泄露不暴露可用令牌。
- 每次请求：读 Cookie → hash → 查 `sessions` → 校验未撤销、未过期 → 取所属 `users`。

## 2. Cookie 安全配置（`KAIROS_SESSION_COOKIE_*`）

| 配置 | 默认（dev） | 说明 |
|---|---|---|
| `KAIROS_SESSION_COOKIE_NAME` | `kairos_session` | Cookie 名 |
| `KAIROS_SESSION_COOKIE_HTTPONLY` | `true` | JS 不可读 |
| `KAIROS_SESSION_COOKIE_SAMESITE` | `lax` | 缓解 CSRF |
| `KAIROS_SESSION_COOKIE_SECURE` | `false` | **local 允许 false；staging/production 必须 true** |
| `KAIROS_SESSION_COOKIE_MAX_AGE_SECONDS` | `604800`（7 天） | Session TTL |

不要为了本地开发关闭整个 Cookie 安全体系：只切 `SECURE=false`，`HTTPONLY`/`SAMEsITE` 保持。

## 3. Session 生命周期

- **建立**：register / login / change_password 成功后下发新 Cookie。
- **登出**：`POST /api/auth/logout` 撤销当前 session 并清除 Cookie。
- **撤销**：`DELETE /api/auth/sessions/{id}`（仅本人）；`POST /api/auth/sessions/logout-others` 撤销除当前外全部。
- **过期**：超过 TTL 后请求被拒（401），需重新登录。
- **撤销/过期后的行为**：任何受保护接口返回 401 `AUTH_REQUIRED`，前端路由守卫跳转 `/login`。

## 4. 修改密码后的 Session 行为（明确策略）

`POST /api/auth/password`：

1. 校验当前密码（失败 → 401 `INVALID_CREDENTIALS`）。
2. 更新 password hash。
3. **撤销除当前外全部 Session**。
4. **当前 Session 一并撤销并轮换**：下发全新令牌 + 新 Cookie。

因此：改密后所有旧令牌（含改密前的当前令牌）立即失效；浏览器拿到新 Cookie 后无需重新登录。

## 5. 登录限流

- `KAIROS_AUTH_LOGIN_MAX_ATTEMPTS=5`、`KAIROS_AUTH_LOGIN_WINDOW_SECONDS=900`。
- 内存滑动窗口按客户端 IP 计数失败登录；超限返回 429 `RATE_LIMITED`。
- 失败统一返回 `INVALID_CREDENTIALS`（同一文案），不泄漏邮箱是否存在。
- 实现位于 `app/auth/rate_limit.py`，通过 `LoginRateLimiter` Protocol 可替换（M-16 全局限流前不使用 Redis）。

## 6. 所有权（ownership）

- 统一守卫 `assert_owned(owner_id, current_user_id)`（`app/auth/errors.py`）。
- 跨用户访问统一返回 **404**（`NOT_FOUND`），不泄漏资源是否存在。
- 所有 repository 查询要求 owner 边界；不允许默认全表查询。
- 后续 M-03+/M-04+ 的业务资源一律复用该守卫与 404 语义。

## 7. 常用本地验证命令

```bash
# 注册（返回 201 + Set-Cookie）
curl -i -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"a@example.com","password":"password123","confirm_password":"password123"}'

# 携带 Cookie 访问受保护接口
curl -i -b kairos_cookie.txt http://localhost:8000/api/auth/me

# 登录
curl -i -c kairos_cookie.txt -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"a@example.com","password":"password123"}'

# 登出
curl -i -b kairos_cookie.txt -c kairos_cookie.txt -X POST http://localhost:8000/api/auth/logout

# 集成 Smoke
cd backend && KAIROS_RUN_INTEGRATION=1 .venv/Scripts/python -m pytest tests/integration/test_auth_smoke.py -m integration
```
