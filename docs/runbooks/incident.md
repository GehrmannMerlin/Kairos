# Kairos Incident Runbook

> M-17 线上故障处置手册（第一版，保持简单）。不引入复杂 Incident taxonomy。

## 1. P0 / P1 定义

- **P0（服务整体不可用 / 数据安全）**：
  - 服务整体不可用（API/登录/任务全挂）。
  - 跨用户数据泄漏风险。
  - 数据严重丢失 / backup-restore 被破坏 / 大量错误写入。
- **P1（核心功能受损）**：
  - 核心 Task 无法运行 / CSV、Evidence 大面积不可用。
  - Worker 持续 crash。
  - Provider / 资源调度系统性故障。
  - disk 即将导致业务停止。

## 2. 处置总顺序

```text
1. Stop blast radius（停止扩大流量/停止写入）
2. Preserve evidence（保留现场与日志）
3. Rollback（若适用，回到上一稳定版本）
4. Fix through Git（Git 分支 → 修复 → 测试 → CI → 新镜像）
5. Redeploy（Staging 验证 → 生产部署）
6. Record incident（记录根因、影响、修复、预防）
```

**禁止**：SSH 进容器 vim 改 Python / 手工替换前端 dist / 直接改数据库“先跑”/ 服务器热改源码。

## 3. 故障场景

### 3.1 大面积 5xx / API 不可用
```bash
DEPLOY_HOST=47.238.145.24 bash infra/scripts/ops-health.sh   # P0/P1 机器可判
ssh deploy@47.238.145.24 'docker logs kairos-api --tail 200'
ssh deploy@47.238.145.24 'docker compose -f /srv/kairos/compose/compose.base.yml -f /srv/kairos/compose/compose.staging.yml ps'
# 若 API 容器反复重启：检查 restart count，回滚到上一稳定镜像
```

### 3.2 登录失败率上升
- 检查 `/health/live`、session cookie 配置、`/srv/kairos/env/staging.env` 中 `KAIROS_SESSION_SECRET` 是否被轮换导致旧会话失效。
- 检查 fail2ban 是否误封（`sudo fail2ban-client status`）。

### 3.3 Worker crash loop
```bash
ssh deploy@47.238.145.24 'docker inspect -f "{{.RestartCount}}" kairos-staging-worker-1'
ssh deploy@47.238.145.24 'docker logs kairos-staging-worker-1 --tail 200'
# 检查 KAIROS_CAPACITY_* / KAIROS_WORKER_ROLES 是否合法；容量配置违规会在 worker 启动即报错
```

### 3.4 Disk pressure
```bash
ssh deploy@47.238.145.24 'df -h | grep -E "/$|/var/lib/docker|kairos"'
# >90% → 停止重型写入；清理旧 backup（保留期内手动删）/ docker builder prune / 旧镜像
# backup 前磁盘不足 → backup.sh 返回 INSUFFICIENT_BACKUP_SPACE，禁止硬写满
```

### 3.5 数据不一致 / cross-user risk
- 立即停止相关 Worker/任务（`docker compose stop worker` 或暂停任务）。
- 保留现场：`docker exec kairos-api python /app/ops_trace.py <task_id>`、收集日志。
- 走 Git 修复 → 新镜像 → Staging 验证 → 再部署；不直接改库。

### 3.6 Evidence / CSV 丢失
- 确认是否为生命周期清理误删（D-072 引用保护应拦截）：检查 retention 脚本 dry-run。
- 从最近 backup bundle 恢复（见 restore runbook）。
- backup 失败：检查 `/srv/kairos/logs/backup-cron.log`，修复后重跑，确认 off-site copy。

## 4. Rollback（回滚）

- 回滚前提：上一稳定镜像仍在（`kairos-api:staging-<sha>`）、发布前备份存在、migration 兼容。
- 方式：`infra/scripts/rollback-staging.sh`（Staging）；Production 按 M-18 部署规范回滚。
- migration 不可逆时：不临场猜回滚，使用前向修复/兼容窗口。

## 5. 记录 Incident

记录：发生时间、P0/P1 级别、现象、根因、修复 commit、验证、预防措施。
写回 `docs/operations/` 或 Git；不静默消失。

## 6. 监控与告警

- 第一版：`infra/scripts/ops-health.sh` 机器可判 PASS/P1/P0（API/DB/Temporal/MinIO/容器/磁盘/最近备份/DB 指标）。
- 告警条件固定（见 ops-health.sh）：API live/ready 异常=P0；容器 down=P0；disk≥90%=P1；restart loop>5=P1；无最近备份=P1。
- 外部通知渠道（短信/邮件/钉钉）后续模块再接，不属 M-17。
