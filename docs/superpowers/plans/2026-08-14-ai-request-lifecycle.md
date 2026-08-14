# AI Request Lifecycle Bugfix Plan（Production）

> 目标：消除 Goal Understanding 误超时、重复模型调用（重复扣费）与「success + error」双显示，
> 建立前后端一致的 AI 请求生命周期与服务器幂等，并默认闭环到 Production。

## 已确认根因（Production 证据）

1. **P0 后端 `/understand` 无幂等**：每次请求都调 DeepSeek + append goal_result。
   Production task 14 两个 goal_result（20229ms / 19810ms，重叠并行）证明重复触发。
2. 前端硬超时 10s < 真实 DeepSeek 20s：客户端 abort + 假「网络请求失败或超时」。
   v0.1.4 已改为 60s，但旧 tab / 缓存仍会命中；且无 Cache-Control 强制刷新。
3. success + error 冲突：客户端 transient 失败覆盖了已持久化的服务器成功。
4. nginx `/api/` 服务器实际配置无 `proxy_read_timeout`（默认 60s），模板 90s 未同步到 Lumina 容器。
5. index.html 无 Cache-Control，浏览器可能长期运行旧 bundle。

## 变更清单

### Backend
- 新表 `understanding_attempts`（migration `0015`）+ 模型 + repository。
  - 身份：`(task_id, source_message_id, input_fingerprint)`；状态 `running|succeeded|failed`。
  - partial unique index（`WHERE status='running'`）= 跨进程并发只允许一个在途。
  - 审计字段：trigger_source / request_id / model_config_id/version / provider / model /
    duration_ms / error_code / result_ref_message_id / result_payload / spec_draft_payload。
  - 不存任何 Secret。
- `GoalUnderstandingService.understand_for_task(user, task_id, trigger_source)`：
  - 计算 source_message_id（最新 user message id）+ input_fingerprint。
  - AUTO 触发：RUNNING → IN_PROGRESS；SUCCEEDED → ALREADY_SUCCEEDED（复用结果，不再调模型）；
    FAILED → 返回既有错误（不自动重试）。
  - USER_REUNDERSTAND：除非 RUNNING，否则新建 attempt（允许新收费）。
  - RUNNING 并发冲突（IntegrityError）→ 降级为 IN_PROGRESS。
  - 失败：append error message + mark FAILED + raise（保持 503 契约）。
- 路由 `POST /tasks/:id/understand` 接受可选 body `{trigger_source}`；响应增加
  `status` / `attempt_id` / `trigger_source`（新增字段，向后兼容）。
- Provider inference timeout 可配置：`KAIROS_PROVIDER_INFERENCE_TIMEOUT_SECONDS`（默认 45s），
  三个 Agent 构造传入 settings（目标理解 / Plan / 语义提取），后端成为有界 Provider 权威。

### Frontend
- `ApiClient`：`RequestOptions.timeoutMs?: number | null`；`null` = 不建自动 timer，
  仍支持外部 AbortSignal；清理 timer/listener。
- `chat.api.ts`：`runUnderstanding(taskId, triggerSource?)` 发送 `{trigger_source}`，保持 AI 60s。
- `TaskChatView.vue`：状态机 idle/understanding/reconciling/success/error；
  - AUTO_INITIAL（页面加载）/ USER_SEND（发送消息）/ USER_REUNDERSTAND（按钮）。
  - IN_PROGRESS / timeout 后 reconcile：有界轮询 getChat 直到 goal_result（每 3s，最多 120s），
    期间显示「模型仍在处理中…」；服务器已成功则不显示 error。
  - 真实 provider/network 错误不被隐藏。

### Infra / Deploy
- 服务器 Lumina nginx：`/api/` 加 `proxy_read_timeout 90s`（staging + production），reload。
- Web nginx：index.html `Cache-Control: no-cache`（hashed assets 保持 immutable）。
- 更新 CLAUDE.md + deployment standard：Production Bugfix Default Closure 规则。
- 默认闭环：PR/CI → Staging → Staging smoke（含慢响应）→ Production → app.kairos.ac.cn 真实验证。

## Timeout Hierarchy（目标）

```
Browser Goal Understanding:  60s 前端安全网（触发 reconcile，非业务失败；可 null+外部 signal）
Reverse Proxy /api/:        90s（服务器实际应用）
Backend API handler:         无独立硬超时（依赖 Provider 有界）
ModelInferenceClient:        KAIROS_PROVIDER_INFERENCE_TIMEOUT_SECONDS = 45s（默认）
DeepSeek read timeout:       httpx 45s（同 Provider overall）
Provider Probe:              前端 45s / 后端 15s（已有契约）
```

## 幂等语义

| 场景 | 行为 |
|---|---|
| 相同 input 两个并发请求 | RUNNING partial unique index → 第二个 IN_PROGRESS，Provider 只调一次 |
| RUNNING 重复请求 | 返回 IN_PROGRESS，不调 Provider |
| SUCCEEDED 重复请求（reload/refresh） | 返回已有 result + spec，不调 Provider |
| FAILED 自动 reload | 不自动重试，返回既有错误 |
| 用户点击「重新理解」 | 允许新 attempt（新模型费用） |

## 测试（先失败后通过）
- Frontend：client null-timeout / external signal / cleanup；chat.api body；TaskChatView
  状态机、IN_PROGRESS 轮询、ALREADY_SUCCEEDED 无 error、重新理解 trigger、慢模型 UX。
- Backend：并发同 input Provider 只调一次；RUNNING/SUCCEEDED/FAILED 复用；re-understand 新 attempt；
  goal_result 唯一；trigger_source 记录；无 Secret 入日志；timeout/network 分类。
- 回归：全量前端单测 + 相关 pytest + ruff/mypy/vue-tsc/build。

## 部署与验证
- Git：`fix/ai-request-lifecycle` → PR → CI → main → release tag `v0.1.5`。
- Staging：deploy-staging.sh → smoke（DeepSeek <10s、>10s、>20s，单 goal_result，refresh 不重复）。
- Production：pre-release backup → deploy-production.sh → migration 0015 → health → 真实网页 smoke。
- 验证 app.kairos.ac.cn 加载新 bundle（asset hash / Cache-Control），nginx 90s，无 5xx，无 Secret。
