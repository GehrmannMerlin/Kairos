# M-17 模块执行记录

状态：**DONE_WITH_PROD_BACKUP_PRECONDITION**（2026-08-12）— 本地 scoped 全绿 + Staging 生产就绪验收全 PASS + 真实 Restore Drill 通过
负责人/Agent：Claude Code
Baseline SHA：`71b926b5235f`（M-16 DONE HEAD，migration 0014）
分支：`feature/M-17-prod-security-backup`（pushed：NO）
依赖模块：M-01～M-16 全部 DONE

## 1. 模块目标
完成 D-019～D-024 正式服务器上线门禁：生产安全基线、网络边界、SSH/HTTPS、Secret/日志脱敏、最小可观测性（structured logs + ops-health + trace 关联）、PostgreSQL/ObjectStorage/config/secret 备份、off-site 备份副本、隔离 Restore Drill、运维 Runbook；不执行 M-18 / DEPLOY-GATE-5 / 不引入 K8s/ELK/Grafana/Redis-for-backup。

## 2. 实施计划
- 使用 superpowers:writing-plans 真实调用；Plan 文件：`docs/superpowers/plans/2026-08-12-m17-production-security-backup-observability.md`（8 个 macro task）。
- Spec Coverage / Placeholder Scan / Type Consistency / Security Boundary / Restore Safety / Scope Check 全部执行。
- **PROJECT SELF-APPROVAL：CHECK 1-22 全部 PASS。**
- **PLAN SELF-APPROVAL：PASS**（28 项全部 PASS）。
- 使用 superpowers:executing-plans 自动执行（Inline Execution，用户预授权）。

## 3. 实现清单
- **安全基线**（Task 1）：`infra/scripts/security-audit.sh`（只读审计）、`check-network-boundary.sh`（仅 22/80/443 公网断言）、`docs/operations/security-baseline.md` + 原始审计输出。服务器事实：Ubuntu 24.04 LTS、公网仅 22/80/443、内部服务零 host 端口、SSH `PasswordAuthentication no / PermitRootLogin no / MaxAuthTries 3`、ufw+fail2ban active、secrets 600、certbot 证书有效 87 天、无 docker.sock/privileged、sudo 仅 certbot+docker restart。**无违规项需改服务器。**
- **Production 配置校验 + Secret scan**（Task 2）：`Settings.production_validation_errors()/validate_runtime()`（production 下 Secure Cookie/真实 CORS/主密钥 64-hex/生产 DB host/独立 bucket+namespace 违规即启动失败），`main.py` 接入；`infra/scripts/secret-scan.sh`（赋值形态真实 secret 聚焦扫描，忽略类型标注/变量引用/模板/canary）；`.gitignore` 增加备份产物与 `.env.production.example`。
- **Observability**（Task 3）：`app/observability/`（contextvars 日志上下文 + `redact_line`/`redact_headers` 逐行脱敏 + OTel trace_id 注入 Filter），API/Worker 统一挂载；OTel collector 增加 metrics pipeline；`ops-health.sh` 机器可读 P0/P1（API live/ready、容器、磁盘≥90%、restart loop、最近备份、DB 业务指标）、`_ops_health.py`（DB 指标）、`ops_trace.py`（Task→Run→Node→Event→Artifact 关联链）。fake canary 验证 0 明文（TEST B）。
- **Backup bundle**（Task 4）：`_backup_common.py`（BackupManifest/flock/disk_preflight/retention）+ `backup.sh`（pg_dump -Fc + MinIO volume 只读 tar + config tar + openssl AES-256 加密 secrets + manifest，含 record_count；磁盘不足 fail-fast；lock 互斥）。所有文件 600 deploy。
- **Off-site copy + Schedule**（Task 5）：`backup-offsite.sh`（本机拉取 + src/dst 聚合 sha256 校验，OFF_SERVER_COPY=PASS）；systemd timer 模板 + **Staging 以 deploy 用户 cron（01:17）启用每日调度**（deploy 无 systemd 权限）。
- **Restore Drill tooling**（Task 6）：`compose.restore-drill.yml`（独立 volume/network，不触碰 staging）+ `restore-drill.sh`（minio 启动前预置对象、pg_restore、migration 一致、5 项验证、自动清理）+ `_restore_verify.py`（async storage 读取）。
- **Production 模板 + Runbook**（Task 7）：`compose.production.yml`（内部零 host 端口、Secret 全 `${VAR:?}`、独立 volume/namespace/bucket、CORS 正式域名）、`zz-kairos-production-tls.conf`、`gen-production-env.sh`、`.env.production.example`；`docs/runbooks/{backup,restore,security-baseline,incident}.md`。
- **Staging 部署**：构建并部署 M-17 api/worker 镜像 `kairos-api/worker:staging-3db026f6c1dd`（M-17 observability + config 校验代码上线），web 不变；同步 otel-collector.yaml。

## 4. Migration
无新增 Migration（M-17 不改 schema；备份含 migration_head=0014 追溯）。

## 5. 本地 scoped 验证（M-17 6 项精华，未重跑历史全量/Golden）
```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/ops -q        # 38 passed (2 flock 用例 Windows 跳过，Staging 真机验证)
# TEST A production config validation         12 passed
# TEST B redaction canary                      4 passed
# TEST C+D backup manifest/lock/disk/retention  8 passed
# TEST E restore contract                      4 passed
# TEST F ops health verdict                    7 passed
# TEST G network contract                      6 passed
.venv/Scripts/python.exe -m ruff check app/observability tests/ops   # PASS
.venv/Scripts/python.exe -m mypy app/observability                   # PASS
cd ..
bash infra/scripts/secret-scan.sh              # SECRET_SCAN_RESULT: PASS
bash -n infra/scripts/*.sh                     # 全部 bash 语法 OK
docker compose -f infra/compose/compose.production.yml config  # 正确要求必填 secret
```

## 6. Staging 生产就绪验收（2026-08-12，非 DEPLOY-GATE-5 / 非 Production Release）
- **Server Security Baseline：PASS**（security-audit.sh + security-baseline.md）。
- **Network Boundary：PASS**（check-network-boundary.sh 输出 NETWORK_BOUNDARY: PASS）。
- **SSH：PASS**（key-only / no root / MaxAuthTries 3 / fail2ban）。
- **HTTPS：PASS**（staging.kairos.ac.cn 证书有效 87 天、HTTP→HTTPS、/api/health/ready ok）。
- **Secret Redaction：PASS**（M-17 staging acceptance 黑盒检查最近 2000 行 api/worker 日志 0 明文 + 本地 TEST B）。
- **Ops Health：PASS**（ops-health.sh 输出 `{"status":"PASS"...}`，api/worker 容器 running，disk 62%，最新备份存在）。
- **Trace Correlation：PASS**（SOURCE_TASK_ID=44：runs=1、events=119、artifacts=1，Task→Run→Node→Artifact 链可定位）。
- **Staging Acceptance 脚本**（api 容器内）：`RESULT=PASS total=6`。

## 7. Backup（真实执行）
- 备份 ID：`staging-20260812-133904-6a423a2a5e18`（git_sha=6a423a2a5e18，migration_head=0014，record_count=81）
- PG dump：PASS（260K，pg_dump -Fc）
- ObjectStorage：PASS（MinIO volume 只读 tar，808K，含业务 bucket + .minio.sys）
- Config：PASS（compose + vhost tar）
- Critical secret backup：PASS（openssl AES-256-CBC 加密 `secrets.env.enc`，解密密钥 `/srv/kairos/env/backup.key` 600）
- Manifest：PASS（无明文 secret，字段完整）
- Checksums：PASS（bundle 内每个产物 .sha256）
- **Off-server copy：PASS**（`OFF_SERVER_COPY=PASS`，src=8ffe82c6… 与 dst 一致；目标为本机受控目录 `~/kairos-offsite-backups/staging`，属 OFF_SERVER_STAGING_DRILL_COPY）
- 自动调度：PASS（deploy 用户 cron 01:17，flock+磁盘 preflight 兜底；systemd timer 为 Production 模板）

## 8. Restore Drill（真实执行）
- 环境：`kairos-restore-drill` 独立 Compose project（独立 volume/network，不绑定域名、不发布端口）**isolated: YES**
- PG restore：PASS；migration 兼容：`MIGRATION_COMPATIBLE 0014`
- 5 项验证（api 镜像一次性容器）：
  1. Task queryable：PASS（task 9）
  2. Record count == backup source：PASS（81 == 81）
  3. FieldEvidence readable：PASS（id 1）
  4. Snapshot content hash match：PASS（sha256 一致）
  5. formal CSV rows + hash：PASS（rows=19，sha256 一致）
- `RESTORE_DRILL=PASS`
- **Staging unaffected：YES**（staging.kairos.ac.cn /api/health/ready ok；drill 容器/volume/network 全部清理）
- 首次失败（`storage.get` 异步未 await）→ 一次性根因修复 → 重跑受影响阶段 PASS；无二次同类失败。

## 9. Git 证据（feature/M-17-prod-security-backup，基线 71b926b，pushed NO）
| Commit | 内容 |
|---|---|
| 78ed2a5 | chore(security): add server audit baseline and network boundary check |
| d5fb51c | feat(ops): enforce production config validation and secret scan |
| 3eb5a22 | feat(observability): add structured logging, redaction, ops health and trace tools |
| 6e68c5d | feat(backup): add versioned backup bundles with manifest, lock and disk preflight |
| 66f4ea6 | feat(backup): add off-site backup copy and daily schedule |
| a32641c | feat(restore): add isolated restore drill tooling |
| 3db026f | chore(deploy): add production templates, network contract test and ops runbooks |

## 10. 明确未做 / 状态
- 未做：M-18 implementation、Production Release、Production DNS（app.kairos.ac.cn 未切）、Production DB/MinIO/namespace 初始化、Production Smoke、Production Tag、DEPLOY-GATE-5、完整渗透测试、完整压测/soak、DEFERRED-DYNAMIC-E2E-01、Push/Merge/Tag。
- **PRODUCTION_OFFSITE_BACKUP = PENDING_EXTERNAL_TARGET**：无长期外部 S3/OSS backup target；M-17 已完成 backup/restore 机制 + Staging drill off-site copy，长期外部 target 作为 M-18 Production preflight 绑定（依 agent-project-implementation-plan M-17 门禁只要求 Restore Drill + readiness checklist；deployment standards §16 的服务器外备份在 M-18 Production 发布前落地）。

## 11. 完成结论
**M-17 = DONE_WITH_PROD_BACKUP_PRECONDITION**（Security/Network/SSH/HTTPS/Secret/Observability/Backup/Off-site/Restore Drill/Runbooks 全 PASS）。
**M-18 = UNBLOCKED_WITH_PRECONDITION**（正式发布前需外部 off-site backup target）。
**DEPLOY-GATE-5 = NOT_REACHED**。
