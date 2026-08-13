# M-16 Capacity Baseline（第一版安全配置基线）

> 状态：**IN_PROGRESS → 待 Staging 补充观测**（2026-08-12）
> 记录 Staging machine context + CapacityConfig + synthetic jobs 观测事实。
> **不是 SLA / benchmark 承诺**；未测项明确列出，不声称真实网页/s 或真实 LLM TPS。

## 定位

这是第一版「资源池安全配置」基线，目标是防止用户/任务占满服务器、资源不足是等待不是
失败、Retry 永远有界、Worker crash 后 slot 可回收。数字来自部署配置（D-071），
**禁止写入 CollectionSpec**。

## CapacityConfig（部署配置，KAIROS_CAPACITY_* 环境变量）

| 项 | 默认值 | 说明 |
|---|---|---|
| `capacity_global_active_tasks` | 4 | 全局同时活跃任务上限（Level 1） |
| `capacity_per_user_active_tasks` | 2 | 单用户同时活跃任务上限（Level 2） |
| `capacity_core_concurrency` | 4 | core/orchestration queue 并发 |
| `capacity_http_concurrency` | 4 | HTTP fetch queue 并发 |
| `capacity_browser_concurrency` | 1 | Browser 队列并发（低并发，安全范围 ≤2） |
| `capacity_llm_search_concurrency` | 2 | LLM/Search queue 并发 |
| `capacity_lease_ttl_seconds` | 120 | 资源 lease TTL（reaper 回收异常退出 worker） |
| `capacity_lease_heartbeat_seconds` | 30 | lease heartbeat 间隔（资源占用事实，非 Checkpoint） |
| `capacity_domain_breaker_threshold` | 5 | 域名连续 domain 级失败数触发熔断 |
| `capacity_domain_breaker_cooldown_seconds` | 60 | 熔断冷却后 HALF_OPEN 单探针 |
| `capacity_default_retry_max_attempts` | 3 | 默认 retry attempt 上限（URL 级优先取 spec） |
| `provider_throttle_min_interval_seconds` | 0.2 | Provider 进程内最小调用间隔 |
| `provider_throttle_max_burst` | 1 | Provider 进程内 burst 上限 |
| `worker_roles` | all | Worker 角色（all / core,http,browser,llm_search） |

启动校验：>0、per-user ≤ global、browser 低并发安全范围、未知 resource class 拒绝。

## Synthetic Jobs（本地，无外部网络）

本地 scoped 验证（`tests/reliability/test_capacity_harness.py`）：

- 提交 12 个 synthetic job，3 用户，global=4 / per-user=2。
- `max_active ≤ global`，无 leaked lease，全部释放。
- 用户公平性：A 超 per-user 后第 3 个任务等待（非失败），B 仍可运行。

> 执行时间 << 1 分钟。Staging 上会再跑一次 10~20 个 lightweight synthetic jobs 并记录观测。

## 明确未测 / 不声称

- 真实网页/s：未测（禁止以测试冒充性能承诺）。
- 真实 LLM TPS / Search QPS：未测。
- 长时间 soak / 大规模并发 / CPU/内存极限压测：未做（M-16 只做 small capacity smoke）。
- Production 容量：未发布，无 Production 数据。

## 结论

第一版安全配置基线（保护服务器不被单用户/单任务占满）。具体 Staging 观测数字在
`docs/implementation/M-16-execution.md` 中补充。
