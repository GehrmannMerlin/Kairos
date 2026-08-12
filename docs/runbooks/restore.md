# Kairos Restore Runbook

> M-17 隔离 Restore Drill 操作手册。restore 在**独立** `kairos-restore-drill` 环境执行，
> 绝不触碰 staging/production volume、网络或域名。
> 相关实现：`infra/scripts/restore-drill.sh` / `_restore_verify.py` / `infra/compose/compose.restore-drill.yml`。

## 1. Prerequisites

- 服务器上存在 backup bundle：`/srv/kairos/backups/<backup_id>/manifest.json`。
- 脚本在 `/srv/kairos/scripts/`（restore-drill.sh、_restore_verify.py）与 `/srv/kairos/compose/compose.restore-drill.yml`。
- api 镜像可拉取/存在（默认 `kairos-api:staging-<sha>`，用于一次性验证容器）。

## 2. 干净目标

restore-drill.sh 自动 `down -v` + 删除 drill volume，每次都是全新环境：
- 独立 volume：`kairos-restore-drill_drill_postgres_data` / `_drill_minio_data`
- 独立 network：`kairos-restore-internal`
- 不发布任何 host 端口、不绑定 `staging.kairos.ac.cn` / `lumina-prod-internal`

## 3. 执行 Restore Drill

```bash
# 服务器上
ssh deploy@47.238.145.24
RESTORE_BACKUP_DIR=/srv/kairos/backups/<backup_id> bash /srv/kairos/scripts/restore-drill.sh
```

脚本按序执行：
1. 读取 manifest（migration_head / record_count）。
2. 重建 drill volume，把 `objects.tar.gz` 解入 fresh MinIO volume（**minio 启动前**，保证元数据一致）。
3. 启动 drill postgres + minio。
4. `pg_restore -U kairos_restore -d kairos_restore --no-owner --no-privileges` 恢复 PG dump。
5. **migration / 版本兼容检查**：manifest.migration_head == drill DB `alembic_version`，不一致即 FAIL。
6. 5 项只读验证（一次性容器，用 api 镜像 + `_restore_verify.py`）：
   - Task 可查询
   - Record count 与 manifest.record_count 一致
   - 一条 FieldEvidence 可读取
   - 一个 PageSnapshot 内容 sha256 与 content_hash 一致
   - 一个 formal CSV Artifact 可下载且 row count / content_hash 正确
7. 验证完成后自动清理（`down -v --remove-orphans` + 删 drill minio volume）。

成功输出：`MIGRATION_COMPATIBLE 0014` + 5 项 `[PASS]` + `RESTORE_DRILL=PASS`。

## 4. Secrets / Config 恢复

- secret 加密副本在 `secrets.env.enc`；解密：`openssl enc -d -aes-256-cbc -pbkdf2 -in secrets.env.enc -out /tmp/restore.env -pass file:/srv/kairos/env/backup.key`（**只在需要迁移/换机时解密**，平时不落明文）。
- config 从 `config/config.tar.gz` 恢复（不含 secret）。
- 恢复后的业务密钥（credential master key）必须与备份点一致，否则已加密凭据不可解。

## 5. 版本兼容性

- restore 的 DB schema 与运行镜像必须匹配：先确认备份点 `git_sha`/`migration_head`，再部署同一版本的 api/worker 镜像。
- 恢复 drill 用当前 staging api 镜像 + 恢复后的 DB/对象验证读取，不做真实 Search/Crawl/LLM/Workflow。

## 6. Health

- drill 只验证数据层可读（Task/Record/Evidence/Snapshot/CSV），不启动业务 API/Worker。
- 真实上线恢复（M-18/迁移）时再按 deployment standards §12 执行 API readiness + smoke。

## 7. 失败处理

| 失败 | 处理 |
|---|---|
| PG restore 失败 | 检查 dump 完整性与 drill PG；重跑受影响阶段（无需重跑对象恢复）。 |
| migration 不一致 | 备份点与镜像版本不匹配；换用同一版本镜像重试。 |
| 5 项验证失败 | 检查对应数据缺失原因（记录/证据/对象）；修复后重跑验证。 |
| 二次同类失败 | 停止，按 M-17 规则 BLOCKED，定位根因。 |

## 8. Cleanup

脚本 `trap cleanup EXIT` 自动删除 drill 容器/volume/网络。若中途强退，手动：

```bash
docker compose -f /srv/kairos/compose/compose.restore-drill.yml --project-name kairos-restore-drill down -v
docker volume rm kairos-restore-drill_drill_minio_data
```

保留：restore report（stdout 记录）、backup manifest、checksum 证据；不删除 off-site backup。
