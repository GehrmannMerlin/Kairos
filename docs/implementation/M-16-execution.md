# M-16 模块执行记录

状态：**DONE**（2026-08-12）— 本地 scoped 验证全绿 + Light Staging Reliability Acceptance 全 PASS
负责人/Agent：Claude Code
Baseline SHA：`841bd9b`（M-15 DONE HEAD，migration 0013）
分支：`feature/M-16-reliability-pools`（pushed：NO）
依赖模块：M-07（Workflow/Activity）、M-09~M-12（Activities/executor 链）、M-15（Artifact 幂等）

## 1. 模块目标
完成 D-013 / D-071 落地：统一 `ErrorClass` 分类 + `RetryDecision`（有界重试、429 Retry-After/jitter、auth/quota 不重试、纠错必须有变化）+ 域名 `CircuitBreaker` + `CapacityConfig`（部署配置，不进 CollectionSpec）+ PostgreSQL 三级 `ResourceAdmission`（全局/单用户/节点资源池）+ ResourceClass→TaskQueue 确定性路由与 Worker 角色 + Provider 限流 + Browser 生命周期安全 + M-15 并发幂等加固 + small capacity smoke。不新增页面、不引入 Redis/Kafka/Kubernetes、DEFERRED-DYNAMIC-E2E-01 不处理。

## 2. 实施计划
- 使用 superpowers:writing-plans 真实调用；Plan 文件：`docs/superpowers/plans/2026-08-12-m16-reliability-resource-pools.md`（8 个 macro task）。
- Spec Coverage / Placeholder Scan / Type Consistency / Scope Check / Determinism Check 全部执行。
- **PROJECT SELF-APPROVAL：CHECK 1-22 全部 PASS。**
- **PLAN SELF-APPROVAL：PASS**（28 项全部 PASS）。
- 使用 superpowers:executing-plans 自动执行（Inline Execution，用户预授权，未启动多层 reviewer 流水线）。

## 3. 实现清单
- **ErrorClass**（`app/reliability/errors.py`）：`ErrorClass(StrEnum)` 12 类（NETWORK_TIMEOUT/TRANSIENT_SERVICE_ERROR/RATE_LIMITED/AUTH_FAILED/QUOTA_EXHAUSTED/STRUCTURE_CHANGED/EXTRACTION_FAILED/QUALITY_FAILED/DOMAIN_UNAVAILABLE/RESOURCE_UNAVAILABLE/CANCELLED/NON_RETRYABLE）；`classify_http_error`/`classify_fetch_error_code`/`classify_provider_error` 映射既有 FetchErrorCode/ProviderError/HTTP status，不建第二套错误框架；`is_domain_breaker_error` 只统计 domain 级错误。
- **RetryDecision**（`app/reliability/retry.py`）：`RetryStrategy`/`RetryDecision`/`decide_retry`（transient backoff、Retry-After、auth/quota user action、correction-change 守卫、资源等待 WAIT_RESOURCE）/`jitter_seconds`（确定性注入）/`correction_fingerprint`/`RetryBudget`/`retry_budget_from`。
- **CircuitBreaker**（`app/reliability/breaker.py` + `domain_circuit_breakers` 表）：CLOSED/OPEN/HALF_OPEN，normalize_domain，OPEN 抑制请求 + 冷却后 HALF_OPEN 单探针（条件 UPDATE 原子认领），404/robots/凭据类不计入，UI 只见脱敏文案。
- **CapacityConfig**（`app/reliability/capacity.py` + `app/config.py` KAIROS_CAPACITY_*）：global/per-user/pool 并发/lease TTL/breaker/retry/provider throttle 默认；启动校验（>0、per-user≤global、browser≤2、未知 class 拒绝）。
- **ResourceAdmission**（`app/reliability/admission.py` + `resource_leases` 表）：Level 1 GLOBAL + Level 2 USER task slot、Level 3 RESOURCE_CLASS pool slot；`pg_advisory_xact_lock` 跨进程原子 acquire；TTL/heartbeat/`reap_expired` 回收；`SlotResult(granted, reason, retry_after)`。
- **TaskQueue 路由 + Worker 角色**（`app/reliability/pools.py` + `worker.py` + `infra/temporal.py`）：`RESOURCE_QUEUE_MAP`（HTTP/BROWSER/LLM_SEARCH 固定常量，CORE→workflow 自身队列）；`workflow_queue_override` 供 workflow 确定性路由；`KAIROS_WORKER_ROLES`（all/core,http,browser,llm_search），每 queue `max_concurrent_activities` 来自 CapacityConfig。
- **WAITING_RESOURCE**（`app/activities/reliability.py` + workflow + executor）：`execute_safe_unit` pool admission → 无 slot 返回 `RESOURCE_WAITING`；`ensure_run_started` task admission → 无 slot 返回 started=False+waiting_reason，任务保持 QUEUED；主循环 heartbeat；`record_resource_wait` 追加 `task.resource_waiting`/`node.resource_waiting` DomainEvent（等待事实，非状态转换、非失败）；Execution Timeline label + Task Drawer 最小徽标。
- **Provider 限流**（`app/reliability/provider_limit.py`）：`ThrottleKey`（family+config_id+user_id sha256，非明文 Key）+ `ProviderLimiter`（min-interval+burst）+ `call_with_provider_retry`；接入 `inference.generate`、`SearchProvider.search`、`fetch._http_with_retry`（后者同时接 breaker + Retry-After，非 retryable 保留原始 FetchErrorCode）。
- **Browser 生命周期**（`app/reliability/browser_pool.py` + `app/crawling/browser.py`）：`BrowserProcessRegistry` + `run_with_browser_slot`（pool limit 上限、finally 释放）；`PlaywrightChromiumRenderer.render` try/finally 关闭 browser（正常/超时/异常）；`close_all_browsers()` 孤儿回收。
- **M-15 并发幂等加固**：`artifacts(request_fingerprint) WHERE status='ready'` partial unique index + `ArtifactService.export` IntegrityError 回滚复用获胜方（不重复生成 Blob）。

## 4. Migration
`alembic/versions/0014_reliability_leases_breaker.py`（additive，head = **0014**，down=0013，可逆）：
- `resource_leases`（scope/scope_key/holder/state/acquired_at/expires_at/heartbeat/released_at/version）
- `domain_circuit_breakers`（domain unique/state/consecutive_failures/open_reason/open_until/half_open_probe_claimed）
- `artifacts` partial unique index `ix_artifacts_user_task_fp_ready`（PG postgresql_where + SQLite sqlite_where）
- 不触碰 M-07 Run / M-09 URLResource / M-13 Record / M-15 Artifact 既有列。

## 5. 验收证据（M-16 scoped，未重跑历史全量 / Golden）
```bash
# backend（tests/reliability 40 passed + artifacts 并发幂等 1 passed；加上联动 scoped：execution/domain/api/activities/plan/crawling/providers/discovery/extraction 全绿）
.venv/Scripts/python.exe -m pytest tests/reliability -q                         # 40 passed
.venv/Scripts/python.exe -m pytest tests/artifacts/test_m16_concurrent_idempotency.py -q  # 1 passed
.venv/Scripts/python.exe -m ruff check app/reliability ...（M-16 全文件 + tests）  # PASS
.venv/Scripts/python.exe -m mypy app/reliability ...（18 files）                  # PASS
.venv/Scripts/python.exe -m alembic heads                                        # 0014 (head)
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"      # import PASS
cd frontend && npx vue-tsc --noEmit && npm run build                             # PASS
cd frontend && npx vitest run src/app/overlay/drawers/TaskStatusDrawer.test.ts    # 4 passed
```
LOCAL DONE GATE 逐项：ErrorClass/RetryDecision/bounded retry/Retry-After/auth no-retry/quota no-retry/correction-change/URL/Node/Domain/Task budget/CircuitBreaker(OPEN suppress + HALF_OPEN recover)/ResourceClass/CapacityConfig/global+user admission/TaskQueue mapping/WAITING_RESOURCE/Provider limiter/Browser limit+cleanup/lease recovery/M-15 concurrent idempotency/small capacity smoke/capacity baseline doc/ruff/mypy/migration consistency/working tree —— 全部 PASS。

## 6. 跨模块联动结果
- M-07 Workflow：PASS（execute_safe_unit 按 ResourceClass 路由；RESOURCE_WAITING/started-wait/heartbeat 分支确定性；fixture workers poll 全部 role queue）。
- M-09/M-10/M-11/M-12 executor 链：PASS（fetch 重试改走 RetryDecision + breaker，原 FetchErrorCode 保留；extract/validate 无回归）。
- M-03 Provider：PASS（inference/search 限流 + 有界重试；ProviderInferenceError 归类 EXTRACTION_FAILED 防盲目重试）。
- M-15 Artifact：PASS（并发导出单 artifact 单 blob；M-15 artifacts 既有测试全绿）。

## 7. Git 证据（feature/M-16-reliability-pools，基线 841bd9b，pushed NO）
| Commit | 内容 |
|---|---|
| 2bd2b4b | feat(worker): add typed retry decisions |
| 269759c | feat(worker): add capacity config with startup validation |
| deb4807 | feat(worker): add domain circuit breakers |
| bd9fc67 | feat(worker): add resource admission leases with recovery |
| cb47c8b | feat(worker): route activities by resource class with waiting semantics |
| 6d3e798 | feat(provider): add bounded provider throttling with retry-after |
| 3221067 | fix(browser): enforce process cleanup and pool limits |
| 3060a07 | docs(worker): record M-16 capacity baseline and execution |

## 8. Staging（Light Reliability Acceptance）
**通过**（2026-08-12，非 DEPLOY-GATE-5 / 非大型压测）：
- 预检查：disk 37G 可用（61%）、migration head 0013、backup `staging-m16-pre-20260812-123203.sql`、rollback 镜像 `staging-cfda95b0bd5c` 保留。
- 部署：只部署受影响的 web/api/worker（WAITING_RESOURCE UI 变化 → web 也 build）；镜像 `kairos-web/api/worker:staging-6a423a2a5e18`；compose.staging.yml 增加 `KAIROS_WORKER_ROLES=all` + 安全小限额（global=4 / per-user=2 / core=2 / http=2 / browser=1 / llm_search=1，lease TTL=60s，breaker threshold=3）。
- Migration：0012→0013→**0014**（head 0014，worker 容器验证 KAIROS_CAPACITY_* 生效）。
- Health：HTTPS 200、live 200、ready postgresql/temporal/object_storage 全 ok。
- Worker roles：`roles=['all'] queues=['kairos-task','kairos-http','kairos-browser','kairos-llm-search']`（多 role queue 连接成功）。
- 验收场景（api 容器内跑 `infra/scripts/_m16_staging_acceptance.py`，连 staging DB，**18/18 PASS**）：
  - A 全局+用户 admission（A 超 per-user 后第 3 任务 WAITING 非 FAILED，B 可运行，global bound）
  - B Browser 资源等待（limit=1：holder 占、waiter `pool_limit` 等待非失败、release 后继续）
  - C Lease Recovery（holder 消失 → reaper 回收 → waiter 可 acquire）
  - D Retry/Circuit（429 尊重 Retry-After、attempt 有界、auth 不重试；breaker OPEN 抑制请求 + 脱敏文案无泄漏）
  - E Capacity Smoke（12 synthetic jobs，max_active=4 ≤ global=4，无 leaked lease，105ms）
- 清理：移除被取代的 M-15 镜像（api 2794a95580e4 / web 58da9e0eb26a），保留 current `6a423a2a5e18` + rollback `cfda95b0bd5c`；`docker builder prune -f`；disk 37G 可用（62%）。
- 容量基线：见 `docs/operations/capacity-baseline.md`。

## 9. 完成结论
**M-16 = DONE**（本地 scoped 全绿 + Light Staging Reliability Acceptance 全 PASS）。**M-17 = UNBLOCKED**。**DEPLOY-GATE-5 = NOT_REACHED**。
明确未做：M-17、M-18、Production、完整可靠性回归、真实大规模压测/soak、DEFERRED-DYNAMIC-E2E-01、Push/Merge/Tag。
