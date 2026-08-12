# Kairos Backup Runbook

> M-17 备份 bundle 操作手册。所有命令在服务器（`deploy` 用户）或本机执行。
> 相关实现：`infra/scripts/backup.sh` / `backup-offsite.sh` / `_backup_common.py`。

## 1. Backup 内容（每个 bundle）

```
<backup_id>/                  staging-YYYYmmdd-HHMMSS-<sha12>
├─ postgres/postgres.dump     pg_dump -Fc 逻辑备份（含 schema + Temporal persistence，同一 PG）
├─ postgres/postgres.dump.sha256
├─ objects/objects.tar.gz     MinIO data volume 只读 tar（含业务 bucket 与 .minio.sys 元数据）
├─ objects/objects.tar.gz.sha256
├─ config/config.tar.gz       部署 compose（不含 secret）
├─ config/vhost.tar.gz        共享 nginx vhost
├─ config/*.sha256
├─ secrets/secrets.env.enc    openssl AES-256-CBC 加密的 env（解密密钥在服务器 /srv/kairos/env/backup.key 0600）
└─ manifest.json              BackupManifest：git_sha / migration_head / record_count / checksums / status
```

规则：manifest 只记录引用，绝无明文 secret；所有备份文件 `600 deploy:deploy`。

## 2. 手动 Backup

```bash
# 服务器上
ssh deploy@47.238.145.24
bash /srv/kairos/scripts/backup.sh
# 退出码 0=成功；2=INSUFFICIENT_BACKUP_SPACE（磁盘不足）；3=lock 被占或错误
```

## 3. 自动 Schedule

- Staging：deploy 用户 cron `17 1 * * *`（见 `crontab -l`），日志 `/srv/kairos/logs/backup-cron.log`。
- Production：M-18 上线前启用 `infra/systemd/kairos-backup.service` + `.timer`（每日 01:17），或等价 cron。
- 并发保护：`flock`（`$BACKUP_DIR/.backup.lock`），第二个 backup 立即退出（exit 3），不会造成磁盘翻倍。

## 4. Backup Destination

- 服务器本地：`/srv/kairos/backups/`（临时）。
- **Off-site（硬要求）**：至少一份复制到服务器之外，否则单机磁盘故障时备份同失。
  - Staging Restore Drill：本机 `backup-offsite.sh` 拉取并校验 checksum。
  - Production：M-18 preflight 绑定长期外部 S3/OSS backup target（当前状态：PENDING_EXTERNAL_TARGET）。

## 5. Off-site Copy（Staging drill）

```bash
# 本机（运行 Claude Code 的工作站）
BACKUP_ID=staging-<id> OFFSITE_LOCAL_DIR=~/kairos-offsite-backups/staging \
  DEPLOY_HOST=47.238.145.24 bash infra/scripts/backup-offsite.sh
# 输出 OFF_SERVER_COPY=PASS（src/dst 聚合 sha256 一致）
```

## 6. Checksum / Manifest 验证

```bash
B=/srv/kairos/backups/<backup_id>
# 校验 bundle 内每个文件 hash
cd $B && for f in $(find . -name '*.sha256'); do sha256sum -c "$f"; done
# manifest 无明文 secret
grep -iE 'password|secret|master_key|api_key' $B/manifest.json || echo "manifest clean"
```

## 7. Retention

- `RETENTION_DAYS`（默认 14）控制服务器本地保留周期；`apply_retention` 只删早于周期的旧目录。
- M-15 lifecycle（业务对象删除）与 Backup retention 是不同语义：业务对象删除后，backup 保留周期内的旧版本仍存在，属 backup policy；M-15 cleanup 不得删 off-site backup。

## 8. 失败处理

| 现象 | 处理 |
|---|---|
| `INSUFFICIENT_BACKUP_SPACE` | 清理旧 bundle / 扩大磁盘；先修磁盘再重试 backup。 |
| lock 被占（exit 3） | 另一个 backup 在跑；等待完成，不要强杀。 |
| pg_dump 失败 | 检查 postgres 容器健康；修复后重跑。 |
| MinIO tar 失败 | 检查 minio 容器与 volume；重跑。 |
| 加密失败 | 检查 `/srv/kairos/env/backup.key` 存在且 600；重跑。 |
| manifest 含明文 | 立即中止，检查为何 secret 进入 manifest；轮换相关 secret。 |

## 9. 审计

每次 backup 在 `/srv/kairos/logs/backup-cron.log`（或手动 stdout）留下 `BACKUP_DONE backup_id=...` 与 `MANIFEST_OK`；manifest 的 `git_sha`/`migration_head`/`record_count` 用于追溯备份点。
