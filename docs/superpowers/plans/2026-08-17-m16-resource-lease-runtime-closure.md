# M-16 Resource Lease Runtime Closure — 实施计划

> 状态：IN_PROGRESS（2026-08-17）
> 分支：`fix/m16-resource-lease-lifecycle`
> 关联：M-16（D-071 三级调度资源租赁生命周期） / M-12 Agent Loop Staging 收口

## 0. 背景

M-12 HYBRID Agent Loop（CONTINUE → Replan → Round 2）已在 Staging 局部验证，但完整循环被
M-16 资源池问题阻塞：`resource_leases` 累积 28682 条 stale 未过期 lease 撑满 CORE pool，
Task 107 的 `generate_artifact` 进入 `WAITING_RESOURCE` 永不恢复。

## 1. 根因（代码证据）

| # | 根因 | 位置 |
|---|---|---|
| C1 | `count_active` 只按 `state=="active"` 计数，不检查 `expires_at > now` → 过期 lease 永久占容量 | `admission.py:49-61` |
| C2 | `LeaseReaper.run_once()` 无生产调用点 → reaper 从未运行 | `admission.py:265-277`（全库无调用） |
| C3 | `heartbeat_pool_slot` 未在生产接线 → 长节点 lease 中途过期 | `admission.py:251`（仅测试调用） |
| C4 | `reap_expired` 无界单条 UPDATE，无 batch / 并发保护 | `admission.py:135-142` |

成因链：worker crash / 取消 → finally 未释放 → lease 保持 `active` 且过期 → 因 C1（不计
expires_at）+ C2（无 reaper）→ 永久累积并占用容量。

## 2. 修复方案

1. **C1**：`count_active` 增加 `expires_at > now` 过滤（`acquire` 传 `now`）。
   过期 lease 即使 reaper 未执行，也不再占用有效容量 → 容量自愈。
2. **C3**：`execute_safe_unit` 执行期间启动 pool slot heartbeat 后台任务，
   `finally` 先取消心跳再释放 lease。保证 C1 启用后长时间节点不被误判可回收。
3. **C2**：worker 运行生命周期接入有界 reaper loop（`asyncio.create_task` + `finally cancel`）。
4. **C4**：`reap_expired` 改为有界批次（默认 500）+ `with_for_update(skip_locked=True)`
   PG 行锁，幂等且并发安全；`release`/reap 均用 `WHERE state=="active"` 条件更新（已幂等）。
5. **取消分类**：`decide_retry` 增加显式 `CANCELLED` 分支（永不重试），锁定既有语义。

不改：HYBRID completion / deduplicate / live activity / resource class 映射（Extract=core 是
设计决定，真正 bug 是 stale lease）。

## 3. 测试

见 `tests/reliability/test_lease_recovery.py` + 新增：
- 过期 lease 不再占容量（C1 核心）
- release 幂等（两次释放安全）
- reaper 不动 fresh lease / 有界批次 / 幂等并发
- pool 满 → 一个 lease 过期 → 容量自动可用（无需手工清理）

## 4. 完成门禁

- ruff / mypy / scoped pytest 全绿
- 合并 main → main CI 镜像 → Staging 部署
- Staging：stale lease 下降、reaper last_run 可观测、WAITING_RESOURCE 自动恢复
- 真实 HYBRID Agent Loop（Round1 → CONTINUE → Round2 → terminal）验证

Production unchanged.
