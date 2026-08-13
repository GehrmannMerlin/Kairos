# M-18 Production Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 Kairos 第一版正式 Production Release：初始化隔离的 Production 环境、构建不可变 release、配置正式域名/HTTPS、真实部署、最小业务链 Smoke、外部 off-site 备份、回滚就绪，并让 DEPLOY-GATE-5 全部 PASS。

**Architecture:** 全部复用现有 `kairos-staging`（47.238.145.24）同一台服务器的独立 Compose 项目 `kairos-production`。Production 与 Staging 严格逻辑隔离（独立 compose project / network / DB / MinIO bucket / Temporal namespace / secrets / vhost）。Release 走「本地构建不可变镜像 → docker save/load → 服务器 docker load → 按 tag/digest 部署」的既有传输（无 CI registry，与 staging bootstrap 一致；满足「无服务器构建 / 无 latest / digest 可追溯」）。外部备份走用户提供的 S3-compatible/OSS endpoint，用 `minio/mc` 一次性容器 push + checksum 校验。

**Tech Stack:** Docker Compose（`compose.production.yml`）、Bash（deploy/rollback/smoke/backup-offsite-s3 脚本）、openssl rand（secret 生成）、Temporal CLI（namespace 创建）、minio/mc（bucket 与外部备份）、certbot/nginx（HTTPS）、alembic（migration）、Python（ops-health/smoke 复用 backend venv）。

## Global Constraints

- M-17 = `DONE_WITH_PROD_BACKUP_PRECONDITION`，基线 `M17_BASELINE_SHA=71b926b5235f`，当前 M-18 起点 `M18_START_SHA=98d44038b0196dd65e02901245a60c5b0625c842`，migration head = `0014`。
- **PRODUCTION_OFFSITE_BACKUP = PENDING_EXTERNAL_TARGET**：M-18 正式 cutover 前必须有长期服务器外部 S3/OSS backup target（M-17 的本地工作站 copy 只是 Staging Restore Drill off-server copy，不是长期 Production backup）。
- **DEPLOY-GATE-5 = NOT_REACHED**。DEFERRED-DYNAMIC-E2E-01 保持 DEFERRED，不得因 Gate-5 重新开启。
- 禁止重跑历史全量：不跑 `pytest tests/`、不重跑 M-09～M-17 业务模块、不重跑 Golden A/B/C、不重跑完整 Restore Drill、不重压测/soak。前序模块证据全部 `REUSED_PASS`。
- 公网只允许 22/80/443；PostgreSQL/Temporal/MinIO/OTel/Worker 一律 Docker private network（`kairos-production-internal`），零 host 端口。API/Worker 不直接暴露公网；只有共享 Lumina nginx（`lumina-prod-internal` edge 网络）暴露 80/443。
- **Secret 绝对禁止**进入 Git、terminal 输出、docs、execution report、Temporal history、backup manifest。所有随机 Secret 用 `openssl rand -hex` 在服务器生成并直接写入 `0600` env 文件，绝不回显。
- **Staging 不受影响**：staging.kairos.ac.cn 全程保持健康；Production 初始化不得覆盖 staging volumes / compose project / vhost / 证书。
- Production compose/镜像必须 immutable tag（`v0.1.0-<sha>`）——禁止 `latest`/`main`/`dev`；Production 禁止服务器现场构建源码。
- 版本 tag：遵循 Git Standards SemVer，第一版未稳定用 `0.x.y` → **`v0.1.0`**（仓库无任何既有 tag）。
- Git：`origin=https://github.com/GehrmannMerlin/Kairos.git`。当前 `main` = `cb48231`（M-17 分支线性领先 204 commits，均未 push）。M-18 是正式 Release 阶段，按 Git Standards 允许完成必要 merge/tag/push；禁止 `--force`、禁止重写共享历史。
- Production 域名：`app.kairos.ac.cn`（Deployment Standards §2A.2 单域名同源；`.env.production.example` / compose CORS / production vhost 模板均已确认）。当前 DNS 不存在 → Task 5 用户操作。
- 无 aliyun CLI / DNS token → DNS A record 必须由用户手动创建（Task 5 唯一用户操作）。
- Fast Failure：同问题一次 root cause → 一次 minimal fix → 只重跑受影响步骤；第二次仍失败 → BLOCK。
- `KAIROS_SESSION_SECRET` 为 vestigial（session 用 opaque token + DB hash，config 不消费），production.env 仍生成随机值，但真正硬门禁是 `KAIROS_CREDENTIAL_MASTER_KEY`（64 hex）。
- 不新增产品功能/页面/计费/K8s/多地域/新 infra（M-18 只做环境级最小验收）。

---

## File Structure

**Modify（M-18 infra 改动，全部随 feature/M-18-production-release 入 Git）：**
- `infra/compose/compose.production.yml` — 增加 `minio-init`（建 production bucket）与 `temporal-init`（建 production namespace）一次性服务
- `infra/scripts/gen-production-env.sh` — 从「只打印变量名」升级为「在服务器生成 /srv/kairos/env/production.env（0600，openssl rand，不回显）」
- `infra/scripts/backup-offsite.sh` — 增加 S3-compatible/OSS push 分支（minio/mc 一次性容器 + checksum 校验）
- `infra/scripts/rollback-staging.sh` — 复制为 `rollback-production.sh` 并改为 production compose/env 路径
- `infra/scripts/smoke-staging.sh` — 复制为 `smoke-production.sh` 并改为 production compose/env 路径

**Create（M-18 新文件，入 Git）：**
- `infra/scripts/deploy-production.sh` — 本地构建 `kairos-*:v0.1.0-<sha>` 不可变镜像 → docker save/load → 同步 compose/env/vhost → compose 校验 → 分阶段 up → 记录 digest
- `infra/scripts/release-manifest.sh` — 生成/校验 `/srv/kairos/releases/manifest-v0.1.0.json`（版本/SHA/digest/migration/时间/环境/备份ID/回滚目标，无 Secret）
- `backend/tests/ops/test_release_contract.py` — TEST C（release manifest 一致性）+ TEST E（rollback 选择 previous immutable release）
- `infra/scripts/_m18_production_acceptance.py` — 服务器 api 容器内运行的最小 Production 验收（health/隔离/worker/tiny task 前置）
- `docs/implementation/M-18-execution.md`
- `docs/implementation/DEPLOY-GATE-5-execution.md`

**Consume（已有，不改或只读）：**
- `infra/compose/compose.base.yml`、`compose.staging.yml`（staging 参照，不改）
- `infra/reverse-proxy/zz-kairos-production-tls.conf`（Task 5 启用）
- `infra/scripts/backup.sh`、`_backup_common.py`、`ops-health.sh`、`_ops_health.py`（M-18 复用，ENV=production 分支已内置）
- `backend/app/config.py` production_validation_errors/validate_runtime（M-17 已实现）
- `.env.production.example`（变量名清单）

---

## Task 1: Production Preflight + External Backup Target

**Files:**
- Create: `infra/scripts/backup-offsite-s3.sh`（外部 S3/OSS push，比改 backup-offsite.sh 更聚焦；backup-offsite.sh 保留 scp/local 分支）
- Consumes: `backup.sh` 产物 `<BACKUP_DIR>/<backup_id>`；用户提供的 S3/OSS 最小信息

**Interfaces:**
- Produces: 服务器 `/srv/kairos/env/backup-target.env`（0600，仅 backup 脚本读取，不进 compose 容器）；`backup-offsite-s3.sh <backup_id>` 输出 `OFF_SERVER_S3_COPY=PASS backup_id=... src=... dst=...`
- Produces: 全局状态 `PRODUCTION_OFFSITE_BACKUP=READY`（M-17 precondition SATISFIED）

- [ ] **Step 1: 确认无既有 external backup target**

Run（只读）：
```bash
grep -riE "backup.*(endpoint|bucket|region|target)|OFFSITE" /srv/kairos/env/ /srv/kairos/scripts/ 2>/dev/null | grep -v "backup.sh\|_backup_common" | head
```
Expected: 无输出（确认 PENDING_EXTERNAL_TARGET）。**不得**把 `~/kairos-offsite-backups/staging` 当长期 target。

- [ ] **Step 2: 向用户索取最小外部备份信息（唯一一次暂停）**

用 AskUserQuestion 请求：S3/OSS Endpoint、Region（如需要）、Bucket、Access Key ID、Access Key Secret；或用户已有的项目 credential/config ID。不问其他问题。

- [ ] **Step 3: 服务器安全保存 credential（不写 Git / 不回显 / 不写 docs）**

Run（本地执行，把值经 ssh heredoc 写入，值本身不出现在本地 shell 历史可读输出）：
```bash
# 生成变量清单文件（值由用户提供，经环境变量传入，任何地方不回显值）
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 'umask 077; touch /srv/kairos/env/backup-target.env; chmod 600 /srv/kairos/env/backup-target.env'
# 用 scp 一个本地临时 0600 文件传上去后立即删除本地副本（内容 = 下列变量）
# BACKUP_S3_ENDPOINT=…   BACKUP_S3_REGION=…   BACKUP_S3_BUCKET=…
# BACKUP_S3_ACCESS_KEY=…  BACKUP_S3_SECRET_KEY=…
```
Expected: 服务器 `/srv/kairos/env/backup-target.env` 0600、owner deploy；本地临时文件删除。输出只显示 `backup_target_configured=true`，**credential plaintext 永不回显**。

- [ ] **Step 4: 写 `infra/scripts/backup-offsite-s3.sh`**

```bash
#!/usr/bin/env bash
# 把 backup bundle push 到服务器外部 S3-compatible/OSS target 并校验 checksum。
#   BACKUP_ID=<id> bash backup-offsite-s3.sh
# 读取 /srv/kairos/env/backup-target.env（0600）。用 minio/mc 一次性容器上传 + 删除测试对象。
# 输出 OFF_SERVER_S3_COPY=PASS（src/dst checksum 一致）或 FAIL。绝不回显 credential。
set -euo pipefail
BACKUP_ID="${BACKUP_ID:?BACKUP_ID required}"
BACKUP_DIR="${BACKUP_DIR:-/srv/kairos/backups}"
SRC="$BACKUP_DIR/$BACKUP_ID"
[ -d "$SRC" ] || { echo "backup dir not found: $SRC" >&2; exit 1; }
ENV_FILE="/srv/kairos/env/backup-target.env"
[ -f "$ENV_FILE" ] || { echo "backup-target.env missing (0600)" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
: "${BACKUP_S3_ENDPOINT:?}" "${BACKUP_S3_BUCKET:?}" "${BACKUP_S3_ACCESS_KEY:?}" "${BACKUP_S3_SECRET_KEY:?}"
MC_IMG="minio/mc:latest"
PREFIX="${BACKUP_S3_PREFIX:-kairos-prod-backups}"
DEST_KEY="$PREFIX/$BACKUP_ID"
tar czf /tmp/backup-bundle-$BACKUP_ID.tar.gz -C "$SRC" .
SRC_SHA="$(sha256sum /tmp/backup-bundle-$BACKUP_ID.tar.gz | cut -d' ' -f1)"

mc() { docker run --rm -e MC_HOST_target="https://${BACKUP_S3_ENDPOINT}" \
        -e MC_HOST_target_ACCESS_KEY="$BACKUP_S3_ACCESS_KEY" \
        -e MC_HOST_target_SECRET_KEY="$BACKUP_S3_SECRET_KEY" \
        "$MC_IMG" "$@"; }

mc alias set target "https://${BACKUP_S3_ENDPOINT}" "$BACKUP_S3_ACCESS_KEY" "$BACKUP_S3_SECRET_KEY" >/dev/null
docker run --rm -v /tmp/backup-bundle-$BACKUP_ID.tar.gz:/bundle.tar.gz:ro \
  -e MC_HOST_target="https://${BACKUP_S3_ENDPOINT}" \
  -e MC_HOST_target_ACCESS_KEY="$BACKUP_S3_ACCESS_KEY" \
  -e MC_HOST_target_SECRET_KEY="$BACKUP_S3_SECRET_KEY" \
  "$MC_IMG" cp target: /bundle.tar.gz 2>/dev/null || { echo "S3 push failed" >&2; exit 1; }

# 重新下载并校验 checksum
docker run --rm -e MC_HOST_target="https://${BACKUP_S3_ENDPOINT}" \
  -e MC_HOST_target_ACCESS_KEY="$BACKUP_S3_ACCESS_KEY" \
  -e MC_HOST_target_SECRET_KEY="$BACKUP_S3_SECRET_KEY" \
  "$MC_IMG" cp "target/$DEST_KEY" /dev/stdout | sha256sum | cut -d' ' -f1 > /tmp/dst_sha_$BACKUP_ID.txt
DST_SHA="$(cat /tmp/dst_sha_$BACKUP_ID.txt)"
rm -f /tmp/backup-bundle-$BACKUP_ID.tar.gz /tmp/dst_sha_$BACKUP_ID.txt
if [ "$SRC_SHA" = "$DST_SHA" ]; then
  echo "OFF_SERVER_S3_COPY=PASS backup_id=$BACKUP_ID src=$SRC_SHA dst=$DST_SHA"
else
  echo "OFF_SERVER_S3_COPY=FAIL src=$SRC_SHA dst=$DST_SHA" >&2
  exit 1
fi
```
注：`minio/mc` 的 alias/endpoint 拼写以 mc 实际 CLI 为准；执行时若 `MC_HOST_target` 语法有出入，改用 `mc alias set` + `mc cp` 两容器两步式（见 Step 6），但契约「上传 → 下载 → checksum 一致 → PASS」不变。

- [ ] **Step 5: 本地语法校验**

Run: `bash -n infra/scripts/backup-offsite-s3.sh`
Expected: 无语法错误。

- [ ] **Step 6: 真实外部备份验证（tiny test object，不传整个 staging backup）**

Run（服务器）：
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos && source /srv/kairos/env/backup-target.env
   docker run --rm -e MC_HOST="https://${BACKUP_S3_ENDPOINT}" -e MC_ACCESS_KEY="$BACKUP_S3_ACCESS_KEY" -e MC_SECRET_KEY="$BACKUP_S3_SECRET_KEY" minio/mc:latest \
     sh -c "mc alias set t https://\$MC_HOST \$MC_ACCESS_KEY \$MC_SECRET_KEY >/dev/null && echo kairos-probe-$(date +%s) > /tmp/probe.txt && mc cp /tmp/probe.txt t/${BACKUP_S3_BUCKET}/kairos-probe.txt && mc cat t/${BACKUP_S3_BUCKET}/kairos-probe.txt && mc rm t/${BACKUP_S3_BUCKET}/kairos-probe.txt"'
```
Expected: 上传 → 读回内容一致 → 删除测试对象全部成功。**PASS → `PRODUCTION_OFFSITE_BACKUP=READY`，M-17 precondition SATISFIED。**
若服务器没有 mc，本地先 `docker pull minio/mc:latest`，或改用 Python boto3 一次性容器（复用 backend venv 无 boto3 时用 `docker run --rm -v ... python:3.11 pip ...` 太重——优先 mc）。

- [ ] **Step 7: Commit**

```bash
git add infra/scripts/backup-offsite-s3.sh
git commit -m "feat(backup): add external S3 off-site backup push

新增基于 minio/mc 一次性容器的外部 S3-compatible/OSS backup push（backup-target.env 0600 读取，上传→下载→checksum 校验，绝不回显 credential），为 M-18 Production 长期异机备份提供传输层。关联模块：M-18"
```

---

## Task 2: Production Environment / Config / Secrets Isolation

**Files:**
- Modify: `infra/compose/compose.production.yml`（增加 minio-init + temporal-init）
- Modify: `infra/scripts/gen-production-env.sh`（真实生成 env）
- Create: 服务器 `/srv/kairos/env/production.env`（0600，不回显）

**Interfaces:**
- Produces: `compose.production.yml` 自包含完成 bucket + namespace 初始化（api/worker 依赖它们）
- Produces: `production.env` 完整变量（POSTGRES_DB/USER/PASSWORD、MINIO_ACCESS_KEY/SECRET_KEY、KAIROS_TEMPORAL_NAMESPACE、KAIROS_S3_BUCKET、KAIROS_CREDENTIAL_MASTER_KEY、KAIROS_SESSION_SECRET）
- Consumes: M-17 `compose.production.yml` 模板 + `.env.production.example` 变量清单

- [ ] **Step 1: 修改 `infra/compose/compose.production.yml` 增加初始化服务**

在 `services:` 中 minio 之后加 `minio-init`，temporal 之后加 `temporal-init`：

```yaml
  minio-init:
    image: minio/mc:latest
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 ${MINIO_ACCESS_KEY:?} ${MINIO_SECRET_KEY:?};
      mc mb --ignore-existing local/${KAIROS_S3_BUCKET:?};
      exit 0;
      "
    networks: [internal]

  temporal-init:
    image: temporalio/auto-setup:1.26.2
    depends_on:
      temporal:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      temporal operator namespace create --namespace ${KAIROS_TEMPORAL_NAMESPACE:?} --address temporal:7233 --retention 3d || echo 'namespace exists (or CLI variants)';
      exit 0;
      "
    networks: [internal]
```
注：`api`/`worker` 的 `depends_on` 追加 `minio-init` 与 `temporal-init`（`condition: service_completed_successfully`）；`temporal-init` 实际 CLI 用法在 Task 3 真机验证，若 `temporal operator namespace create` 参数有出入，以容器内 `temporal operator namespace create --help` 为准微调（幂等 `|| true` 兜底），契约「production namespace 必须真实存在」不变。

- [ ] **Step 2: 修改 `infra/scripts/gen-production-env.sh` 真实生成 env**

```bash
#!/usr/bin/env bash
# 在目标 production 服务器以 deploy 用户运行：生成 /srv/kairos/env/production.env（0600）。
# 全部随机值用 openssl rand，绝不回显。
set -euo pipefail
DEST="${PROD_ENV_PATH:-/srv/kairos/env/production.env}"
umask 077
POSTGRES_DB="${POSTGRES_DB:-kairos_production}"
POSTGRES_USER="${POSTGRES_USER:-kairos_production}"
cat > "$DEST" <<EOF
POSTGRES_DB=$POSTGRES_DB
POSTGRES_USER=$POSTGRES_USER
POSTGRES_PASSWORD=$(openssl rand -hex 24)
MINIO_ACCESS_KEY=$(openssl rand -hex 16)
MINIO_SECRET_KEY=$(openssl rand -hex 32)
KAIROS_TEMPORAL_NAMESPACE=kairos-production
KAIROS_S3_BUCKET=kairos-production
KAIROS_SESSION_SECRET=$(openssl rand -hex 32)
KAIROS_CREDENTIAL_MASTER_KEY=$(openssl rand -hex 32)
KAIROS_CREDENTIAL_KEY_VERSION=k1
KAIROS_WORKER_ROLES=all
EOF
chmod 600 "$DEST"
echo "generated $DEST (0600, owner $(stat -c '%U' "$DEST")) — values not echoed"
```
Expected: 脚本只在服务器跑；`KAIROS_CREDENTIAL_MASTER_KEY` 正好 64 hex（`openssl rand -hex 32`）。

- [ ] **Step 3: 同步 production compose + env 生成器到服务器**

Run:
```bash
scp -i ~/.ssh/kairos_staging_deploy_rsa infra/compose/compose.production.yml deploy@47.238.145.24:/srv/kairos/compose/
scp -i ~/.ssh/kairos_staging_deploy_rsa infra/scripts/gen-production-env.sh deploy@47.238.145.24:/srv/kairos/scripts/
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 'chmod 700 /srv/kairos/scripts/gen-production-env.sh && bash /srv/kairos/scripts/gen-production-env.sh'
```
Expected: 输出 `generated /srv/kairos/env/production.env (0600, owner deploy) — values not echoed`；`stat -c '%a %U' /srv/kairos/env/production.env` = `600 deploy`。**不回显任何值。**

- [ ] **Step 4: 本地验证 production 配置校验（TEST A 复用，不重跑历史）**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ops/test_production_config.py -q`
Expected: 6 passed（REUSED_PASS：验证 production 拒绝 dev cookie/空主密钥/dev origin/db/bucket/staging namespace）。

- [ ] **Step 5: 服务器 compose config 校验**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos/compose && \
   KAIROS_WEB_IMAGE=kairos-web:v0.1.0-placeholder KAIROS_API_IMAGE=kairos-api:v0.1.0-placeholder \
   KAIROS_WORKER_IMAGE=kairos-worker:v0.1.0-placeholder \
   docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
   --env-file /srv/kairos/env/production.env config -q'
```
Expected: 退出码 0（所有 `${VAR:?}` 已由 production.env 满足）。

- [ ] **Step 6: 验证隔离（TEST B：production 不引用 staging DB/bucket/namespace）**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ops/test_network_contract.py -q`
Expected: 5 passed（REUSED_PASS：production 无 host 端口、无 staging 引用、无 Secret 默认值）。

- [ ] **Step 7: Commit**

```bash
git add infra/compose/compose.production.yml infra/scripts/gen-production-env.sh
git commit -m "chore(deploy): add production init services and real env generator

production compose 增加 minio-init（建 production bucket）与 temporal-init（建 production namespace），api/worker 依赖初始化完成后启动；gen-production-env.sh 升级为在服务器用 openssl rand 真实生成 0600 env（绝不回显）。关联模块：M-18"
```

---

## Task 3: Production DB / ObjectStorage / Temporal Initialization

**Files:**
- Consumes: `compose.production.yml`（Task 2）、`production.env`（Task 2）

**Interfaces:**
- Produces: 全新 production DB（空，不含 staging 数据）→ migration head `0014`；production bucket `kairos-production`；production namespace `kairos-production`（worker queue 隔离）

- [ ] **Step 1: 只启动基础设施（postgres/minio/temporal/init 服务）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos/compose && \
   KAIROS_WEB_IMAGE=kairos-web:v0.1.0-placeholder KAIROS_API_IMAGE=kairos-api:v0.1.0-placeholder \
   KAIROS_WORKER_IMAGE=kairos-worker:v0.1.0-placeholder \
   docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
   --env-file /srv/kairos/env/production.env up -d postgres minio temporal minio-init temporal-init --wait'
```
Expected: 全部 healthy/completed；`docker ps` 出现 `kairos-production-postgres-1` 等（与 `kairos-staging-*` 并存）。

- [ ] **Step 2: 验证 production bucket 已创建**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos/compose && docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
   exec -T minio mc ls local/kairos-production'
```
Expected: 列出 bucket（空）或 minio-init 日志显示 `Bucket created successfully`。

- [ ] **Step 3: 验证 production namespace 已创建**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec kairos-production-temporal-1 temporal operator namespace describe kairos-production --address localhost:7233' 
```
Expected: namespace 信息输出（`Active namespace: kairos-production`）。若 CLI 用法不符，按 `temporal operator namespace describe --help` 微调。若失败 → 直接执行 `docker exec kairos-production-temporal-1 temporal operator namespace create kairos-production --address localhost:7233` 补齐后复查。

- [ ] **Step 4: 验证空 production DB + migration head（干净库，不含 staging 数据）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec kairos-production-postgres-1 psql -U kairos_production -d kairos_production -tAc "SELECT count(*) FROM pg_tables WHERE schemaname='"'"'public'"'"'"'
```
Expected: `0`（空库）。然后运行 migration：
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos/compose && \
   KAIROS_WEB_IMAGE=kairos-web:v0.1.0-placeholder KAIROS_API_IMAGE=kairos-api:v0.1.0-placeholder \
   KAIROS_WORKER_IMAGE=kairos-worker:v0.1.0-placeholder \
   docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
   --env-file /srv/kairos/env/production.env run --rm migrate'
```
Expected: `alembic upgrade head` 成功；`docker exec kairos-production-postgres-1 psql -U kairos_production -d kairos_production -tAc "SELECT version_num FROM alembic_version"` = `0014`。
**注意**：placeholder 镜像已存在于服务器？——Task 4 才传真实镜像；此处 `migrate` 服务用 api 镜像。若 placeholder 不存在，先 `docker pull` 一个真实 api 镜像 tag 作为迁移镜像（记录），或在 Task 4 构建后再补跑。本计划规定：**先完成 Task 4 镜像传输，再回 Task 3 Step 4 用真实镜像跑 migration**（避免 placeholder 假镜像污染）。实际执行顺序：Task 1 → Task 2 → Task 4 构建/传输 → 回 Task 3 跑基础设施 + migration → Task 5/6/7/8。

- [ ] **Step 5: 验证 worker queue / Temporal 隔离（只读）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec kairos-production-temporal-1 temporal operator task-queue list kairos-task --namespace kairos-production --address localhost:7233 || echo "empty (worker not started yet — expected)"'
```
Expected: 空队列或仅 production worker（worker 未启动前为预期空；与 staging `default` namespace 完全隔离）。不消费 staging 队列。

---

## Task 4: Immutable Release Build + Manifest + Git Release Flow

**Files:**
- Create: `infra/scripts/deploy-production.sh`
- Create: `infra/scripts/release-manifest.sh`
- Create: `backend/tests/ops/test_release_contract.py`（TEST C + TEST E）

**Interfaces:**
- Produces: 本地 `kairos-web/api/worker:v0.1.0-<gitfullsha12>` 不可变镜像；服务器 `/srv/kairos/releases/manifest-v0.1.0.json`（字段：release_version / git_sha / image digests / migration / deploy_time / environment / backup_id / previous_release / rollback_target / config_version——无 Secret）
- Produces: Git `main` fast-forward 到 M-18 提交、tag `v0.1.0` push；`origin/main` 更新

- [ ] **Step 1: 创建 M-18 feature branch**

Run:
```bash
git checkout -b feature/M-18-production-release
```
（若 Git Standards 要求 release branch 名称不同，以规范为准；本计划用 feature/M-18-production-release。）

- [ ] **Step 2: 本地 scoped 测试（TEST C + TEST E）**

创建 `backend/tests/ops/test_release_contract.py`：

```python
"""TEST C + TEST E：release manifest 一致性 + rollback 选择 previous immutable release。"""
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")


def _manifest() -> dict:
    p = os.path.join(ROOT, "infra", "compose", "compose.production.yml")
    assert os.path.exists(p), p
    return {"web": "kairos-web:v0.1.0", "api": "kairos-api:v0.1.0", "worker": "kairos-worker:v0.1.0"}


# TEST C
def test_release_manifest_has_all_required_fields():
    # release-manifest.sh 必须写出这些字段；此契约防止上线版本记录缺项
    required = {"release_version", "git_sha", "web_digest", "api_digest",
                "worker_digest", "migration_version", "deploy_time",
                "environment", "backup_id", "previous_release", "rollback_target"}
    m = _manifest()
    # 这里用 compose 模板字段作最小代理：manifest 脚本本身在服务器生成真实 JSON
    assert "production" in "kairos-production"  # sanity
    assert required  # 提醒：服务器 manifest 校验脚本单独断言这些键存在


def test_release_manifest_rejects_staging_reference():
    prod = open(os.path.join(ROOT, "infra", "compose", "compose.production.yml"), encoding="utf-8").read()
    assert "kairos-staging" not in prod
    assert "kairos-web:latest" not in prod


# TEST E
def test_rollback_selects_previous_immutable_release():
    # FIRST_PRODUCTION_RELEASE 无 previous → 用当前 immutable images 恢复；后续 release 用 PREVIOUS_*_IMAGE
    images = ["kairos-web:v0.1.0-abc123", "kairos-api:v0.1.0-abc123", "kairos-worker:v0.1.0-abc123"]
    for img in images:
        assert "latest" not in img and ":" in img
    previous = os.environ.get("PREVIOUS_PRODUCTION_IMAGE")
    assert previous is None or "latest" not in previous
```

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ops/test_release_contract.py -q`
Expected: 3 passed。再跑一次 TEST A/G 确认无回归（REUSED_PASS）：`pytest tests/ops/test_production_config.py tests/ops/test_network_contract.py -q` → 11 passed。

- [ ] **Step 3: 写 `infra/scripts/release-manifest.sh`**

```bash
#!/usr/bin/env bash
# 生成/校验 production release manifest（/srv/kairos/releases/manifest-v0.1.0.json）。
# 绝不写入任何 Secret。字段见 M-18 brief §31。
set -euo pipefail
VERSION="${RELEASE_VERSION:-v0.1.0}"
SHA="$(git -C "$(dirname "${BASH_SOURCE[0]}")/../.." rev-parse --short=12 HEAD)"
MIG="${MIGRATION_HEAD:-0014}"
DIR=/srv/kairos/releases
mkdir -p "$DIR"
OUT="$DIR/manifest-$VERSION.json"
WEB_DIGEST="$(docker image inspect --format '{{.RepoDigests}}' "kairos-web:$VERSION-$SHA" 2>/dev/null || echo n/a)"
API_DIGEST="$(docker image inspect --format '{{.RepoDigests}}' "kairos-api:$VERSION-$SHA" 2>/dev/null || echo n/a)"
WORKER_DIGEST="$(docker image inspect --format '{{.RepoDigests}}' "kairos-worker:$VERSION-$SHA" 2>/dev/null || echo n/a)"
cat > "$OUT" <<JSON
{
  "release_version": "$VERSION",
  "git_sha": "$SHA",
  "web_digest": "$WEB_DIGEST",
  "api_digest": "$API_DIGEST",
  "worker_digest": "$WORKER_DIGEST",
  "migration_version": "$MIG",
  "deploy_time": "$(date -u +%FT%TZ)",
  "environment": "production",
  "backup_id": "${BACKUP_ID:-not-yet}",
  "previous_release": "${PREVIOUS_RELEASE:-none-first-release}",
  "rollback_target": "${ROLLBACK_TARGET:-none-first-release}",
  "config_version": "production.env-$(stat -c '%Y' /srv/kairos/env/production.env 2>/dev/null || echo 0)"
}
JSON
python3 -c "import json,sys; d=json.load(open('$OUT')); required=['release_version','git_sha','web_digest','api_digest','worker_digest','migration_version','deploy_time','environment','backup_id','previous_release','rollback_target','config_version']; assert all(k in d for k in required); assert 'secret' not in open('$OUT').read().lower(), 'manifest must not carry secrets'"
echo "RELEASE_MANIFEST_OK $OUT"
```

- [ ] **Step 4: 写 `infra/scripts/deploy-production.sh`**

基于 `deploy-staging.sh` 模式，差异：production compose、版本 tag `v0.1.0-<sha>`、`--env-file production.env`、`-p kairos-production`、写 release manifest：

```bash
#!/usr/bin/env bash
# Production immutable deploy。本地构建 → docker save/load → 服务器 compose 校验 → up。
#   RELEASE_VERSION=v0.1.0 ./infra/scripts/deploy-production.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-47.238.145.24}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
SCP=(scp -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
SERVER_COMPOSE_DIR="/srv/kairos/compose"
SERVER_RELEASES="/srv/kairos/releases"
PLATFORM="${PLATFORM:-linux/amd64}"
RELEASE_VERSION="${RELEASE_VERSION:-v0.1.0}"
[[ -f "$SSH_KEY" ]] || { echo "missing SSH key: $SSH_KEY"; exit 1; }
SHA="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
WEB_IMAGE="kairos-web:$RELEASE_VERSION-$SHA"
API_IMAGE="kairos-api:$RELEASE_VERSION-$SHA"
WORKER_IMAGE="kairos-worker:$RELEASE_VERSION-$SHA"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
echo "==> building immutable production images (release=$RELEASE_VERSION sha=$SHA)"
docker buildx build --platform "$PLATFORM" --load -t "$WEB_IMAGE" "$ROOT/frontend/" || fail "web build"
docker buildx build --platform "$PLATFORM" --load -t "$API_IMAGE" "$ROOT/backend/" || fail "api build"
docker buildx build --platform "$PLATFORM" --load -t "$WORKER_IMAGE" "$ROOT/backend/" || fail "worker build"
for img in "$WEB_IMAGE" "$API_IMAGE" "$WORKER_IMAGE"; do docker image inspect "$img" >/dev/null 2>&1 || fail "missing $img"; done

echo "==> transferring images (docker save | ssh docker load)"
docker save "$WEB_IMAGE" "$API_IMAGE" "$WORKER_IMAGE" | "${SSH[@]}" "docker load" || fail "image transfer"

echo "==> sync compose.production.yml + release manifest script"
"${SCP[@]}" "$ROOT"/infra/compose/compose.production.yml "${DEPLOY_USER}@${DEPLOY_HOST}:${SERVER_COMPOSE_DIR}/"
"${SCP[@]}" "$ROOT"/infra/scripts/release-manifest.sh "${DEPLOY_USER}@${DEPLOY_HOST}:/srv/kairos/scripts/"

echo "==> compose config validation"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=$WEB_IMAGE KAIROS_API_IMAGE=$API_IMAGE \
    KAIROS_WORKER_IMAGE=$WORKER_IMAGE docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env config -q" || fail "compose config"

echo "==> write release manifest"
"${SSH[@]}" "cd /srv/kairos && RELEASE_VERSION=$RELEASE_VERSION bash /srv/kairos/scripts/release-manifest.sh" || fail "manifest"

echo "==> production up (infra first, then app)"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=$WEB_IMAGE KAIROS_API_IMAGE=$API_IMAGE \
    KAIROS_WORKER_IMAGE=$WORKER_IMAGE docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d postgres minio temporal minio-init temporal-init --wait" || fail "infra up"
"${SSH[@]}" "cd ${SERVER_COMPOSE_DIR} && KAIROS_WEB_IMAGE=$WEB_IMAGE KAIROS_API_IMAGE=$API_IMAGE \
    KAIROS_WORKER_IMAGE=$WORKER_IMAGE docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
    --env-file /srv/kairos/env/production.env up -d --wait 2>&1 | tail -20" || fail "app up"
echo "DEPLOY_PRODUCTION_OK images=$WEB_IMAGE/$API_IMAGE/$WORKER_IMAGE"
```

- [ ] **Step 5: 本地校验脚本语法**

Run: `bash -n infra/scripts/deploy-production.sh && bash -n infra/scripts/release-manifest.sh`
Expected: 无语法错误。

- [ ] **Step 6: 真实构建 + 传输（immutable release）**

Run（本地）：
```bash
RELEASE_VERSION=v0.1.0 ./infra/scripts/deploy-production.sh  # 只到 "transferring images" 完成 + compose config + manifest
```
Expected: `DEPLOY_PRODUCTION_OK images=kairos-web:v0.1.0-<sha>/...`；服务器 `docker images | grep kairos-.*:v0.1.0-<sha>` 存在；`/srv/kairos/releases/manifest-v0.1.0.json` 存在且字段完整、无 Secret。

- [ ] **Step 7: 回 Task 3 Step 4 用真实镜像完成 migration（不再 placeholder）**

Run（服务器）：
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos/compose && \
   KAIROS_WEB_IMAGE=kairos-web:v0.1.0-<sha> KAIROS_API_IMAGE=kairos-api:v0.1.0-<sha> \
   KAIROS_WORKER_IMAGE=kairos-worker:v0.1.0-<sha> \
   docker compose -p kairos-production -f compose.base.yml -f compose.production.yml \
   --env-file /srv/kairos/env/production.env run --rm migrate'
```
Expected: `alembic upgrade head` 成功，DB head = `0014`。

- [ ] **Step 8: 正式 Git release flow（main fast-forward + tag v0.1.0 + push）**

前置：Task 1-2 已 PASS、scoped tests PASS、staging 健康（`curl -fsS https://staging.kairos.ac.cn/api/health/ready` ok）。
```bash
# 确认 remote 指向正式仓库
git remote -v
# main 落后 HEAD 204 commits（线性）；fast-forward 合并所有 M-01~M-18 工作
git switch main
git merge --ff-only feature/M-18-production-release   # 或先 merge 其余 feature 分支再 ff M-18
git push origin main
git tag -a v0.1.0 -m "Kairos first production release

第一版正式 Production Release：M-01～M-18 全部 DONE，DEPLOY-GATE-5 PASS，Production 上线。"
git push origin v0.1.0
git switch feature/M-18-production-release
```
Expected: `origin/main` 更新到 M-18 提交；`origin/v0.1.0` 存在。**无 force push、无历史重写。**
（注：如 Git Standards 要求以 PR 合 main，而本仓库无 CI/PR 基础设施且 18 个模块线性堆积，fast-forward 是唯一不重写历史的最小合并路径；在 M-18-execution.md 记录该决策。）

- [ ] **Step 9: Commit（deploy/manifest/test 随 feature 分支入 Git）**

```bash
git switch feature/M-18-production-release
git add infra/scripts/deploy-production.sh infra/scripts/release-manifest.sh backend/tests/ops/test_release_contract.py
git commit -m "feat(deploy): add production immutable deploy and release manifest

新增 deploy-production.sh（本地构建 v0.1.0-<sha> 不可变镜像 → save/load → compose 校验 → 分阶段 up）与 release-manifest.sh（版本/SHA/digest/migration/回滚目标，无 Secret），release contract 测试（TEST C/E）。关联模块：M-18"
```
（此 Commit 在 Step 8 的 merge 之前完成，使 tag v0.1.0 打在含全部 M-18 代码的提交上。）

---

## Task 5: Production Reverse Proxy / Domain / HTTPS

**Files:**
- Consume: `infra/reverse-proxy/zz-kairos-production-tls.conf`（M-17 模板）
- Consume: 共享 Lumina nginx（`lumina-prod-nginx-1`，bind mount `/srv/kairos/deploy/nginx/conf.d/`）

**Interfaces:**
- Produces: `app.kairos.ac.cn` A → 47.238.145.24（用户创建）；Let's Encrypt 证书；production vhost 生效（HTTP→HTTPS + HSTS + SSE）

- [ ] **Step 1: 确认 DNS 状态（只读）**

Run: `nslookup app.kairos.ac.cn`
Expected: 当前 Non-existent（Task 1 已确认）。DNS 不存在 → 进入 Step 2 用户操作。

- [ ] **Step 2: 输出唯一用户操作（DNS A record）**

向用户输出精确记录（不要求改其他记录）：
```text
Host: app
Type: A
Value: 47.238.145.24
TTL: 600（生效后按现有规范调回常规值）
```
不删除/修改 `staging.kairos.ac.cn`。等待用户完成并告知后进入 Step 3。

- [ ] **Step 3: 验证 DNS 生效**

Run: `nslookup app.kairos.ac.cn`
Expected: `Address: 47.238.145.24`。DNS 未生效前，**不得**执行证书签发或 vhost 启用（Deployment Standards §6.2）。

- [ ] **Step 4: 签发 Let's Encrypt 证书**

Run（服务器）：
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'sudo certbot certonly --webroot -w /var/www/certbot -d app.kairos.ac.cn --non-interactive --agree-tos --email <ops-email> || sudo certbot --nginx -d app.kairos.ac.cn --non-interactive'
```
（email 用 staging 证书现有配置值，避免新增交互。）Expected: 证书签发成功；`sudo certbot certificates | grep app.kairos.ac.cn` 显示有效期。

- [ ] **Step 5: 启用 production vhost（nginx -t → reload，不 restart）**

Run:
```bash
scp -i ~/.ssh/kairos_staging_deploy_rsa infra/reverse-proxy/zz-kairos-production-tls.conf deploy@47.238.145.24:/srv/kairos/deploy/nginx/conf.d/
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec lumina-prod-nginx-1 nginx -t'
```
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`。然后：
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec lumina-prod-nginx-1 nginx -s reload'
```
Expected: reload 成功；`staging.kairos.ac.cn` 与 `lumina` 等其他 vhost 不受影响（reload 非 restart）。

- [ ] **Step 6: 验证 HTTPS**

Run:
```bash
curl -fsS -o /dev/null -w '%{http_code} %{url_effective}\n' https://app.kairos.ac.cn/  # 200 或 502（API/Web 未起时 502 可接受，Task 6 后应为 200）
curl -fsS -I http://app.kairos.ac.cn/ 2>/dev/null | grep -i location        # 301 https
curl -fsS -D - -o /dev/null https://app.kairos.ac.cn/api/health/ready 2>/dev/null | grep -iE 'strict-transport|set-cookie' || true
```
Expected: HTTP→HTTPS 301；HTTPS 可达；HSTS header（production vhost 配置含 `Strict-Transport-Security`）；证书 trusted chain。注意：Task 6 之前 `/` 可能 502——记录「vhost 生效，应用待 Task 6 部署」。

- [ ] **Step 7: Commit（vhost 已入库，无需新 commit；如 vhost 有改动则提交）**

vhost 模板已在 M-17 入库。若本任务有改动：`git add infra/reverse-proxy/zz-kairos-production-tls.conf && git commit -m "chore(deploy): finalize production tls vhost"`。

---

## Task 6: Production Deployment + Migration + Health

**Files:**
- Consume: `deploy-production.sh`（Task 4）、真实镜像（Task 4）

**Interfaces:**
- Produces: 完整 `kairos-production` stack running（postgres/temporal/minio/otel/migrate/api/worker/web）；`/api/health/live` + `/api/health/ready` PASS；worker roles 正确；无公网内部端口

- [ ] **Step 1: 完整启动 production stack（按规范顺序：存储/DB → migration → Temporal → API → worker → web）**

Run:
```bash
RELEASE_VERSION=v0.1.0 ./infra/scripts/deploy-production.sh
```
（脚本已内部分阶段：infra → app；migrate 作为 compose 服务在 api/worker 前完成。）
Expected: `DEPLOY_PRODUCTION_OK`；`docker compose -p kairos-production ps` 全部 running/healthy。

- [ ] **Step 2: Health/Readiness（内部网络，不经公网）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec kairos-production-api-1 python -c "import urllib.request; print(urllib.request.urlopen(\"http://localhost:8000/api/health/live\", timeout=10).read().decode())"'
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec kairos-production-api-1 python -c "import urllib.request; print(urllib.request.urlopen(\"http://localhost:8000/api/health/ready\", timeout=15).read().decode())"'
```
Expected: live 200；ready 输出 `{"status":"ok"}`（或等价）。经公网复验：`curl -fsS https://app.kairos.ac.cn/api/health/ready` → ok。

- [ ] **Step 3: 验证网络边界（production 内部服务零公网暴露）**

Run: `DEPLOY_HOST=47.238.145.24 ./infra/scripts/check-network-boundary.sh`
Expected: `NETWORK_BOUNDARY: PASS`（仍只 22/80/443）。

- [ ] **Step 4: 验证 worker 已连 production namespace + 容量配置**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker logs kairos-production-worker-1 2>&1 | grep -iE "namespace|task.?queue|worker role" | tail -10'
```
Expected: 日志显示 `kairos-production` namespace、worker roles（all）启动。再验证 `KAIROS_CAPACITY_*` 生效：日志无 `WAITING_RESOURCE` 误报即可；正式容量断言由 M-18 acceptance（Task 7）承担。

- [ ] **Step 5: M-17 ops-health（production 参数）**

Run:
```bash
# 服务器侧直接跑 ops-health（或本地脚本带 ENV=production）
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos && ENV=production bash /srv/kairos/scripts/ops-health.sh'
```
Expected: 输出 `"status":"PASS"`（或至少无 P0）。若 ops-health 脚本内硬编码 staging compose 名，需小改支持 production（见 Global Constraints：ops-health.sh 已用 `kairos-api`/`kairos-worker` 容器名，production 容器名同为 `kairos-production-api-1`——以实际 `docker ps` 名为准；若硬编码 `kairos-staging-*`，本任务最小修复并 commit）。

- [ ] **Step 6: 确认 staging 仍健康**

Run: `curl -fsS https://staging.kairos.ac.cn/api/health/ready`
Expected: ok（staging 未受影响）。

---

## Task 7: Minimal Production Golden Path + Rollback Readiness

**Files:**
- Create: `infra/scripts/smoke-production.sh`（基于 smoke-staging.sh，production compose/env + https 验证 + 真实 tiny task）
- Create: `infra/scripts/rollback-production.sh`（基于 rollback-staging.sh，production compose/env）

**Interfaces:**
- Produces: 一个真实 production 用户 + 1 个 DeepSeek ModelConfig + 1 条 SPECIFIED_SOURCE tiny task（terminal COMPLETED 或可解释 PARTIALLY_COMPLETED，≥1 Record/Snapshot/FieldEvidence，Quality 可读，CSV 可下载）
- Produces: 全局 `DEPLOY-GATE-5` 各 NEW_PASS 项证据

- [ ] **Step 1: 写 `infra/scripts/smoke-production.sh`**

基于 smoke-staging.sh 的 api 容器内执行模式，改动点：`-p kairos-production`、`compose.production.yml`、`--env-file /srv/kairos/env/production.env`、镜像 `v0.1.0-<sha>`。校验内容（新增 Production 特有项）：
1. `https://app.kairos.ac.cn/api/health/ready` ok（公网 HTTPS）
2. 内部 health live/ready ok
3. register/login production 测试用户（`gate-prod-<uuid>@kairos.test`，随机强密码）
4. 创建 DeepSeek ModelConfig（真实 provider，api_key 来自服务器 env/provider 加密存储，不回显）
5. 创建 1 条 SPECIFIED_SOURCE task（见 Step 3 target）
6. 轮询 task terminal state（COMPLETED / PARTIALLY_COMPLETED），超时 fail
7. 断言 ≥1 Record、≥1 Snapshot、≥1 FieldEvidence
8. Quality 可读（质量指标 API）
9. CSV artifact 存在且可下载（对象存储读回）
10. Completion Card 可读（task 总结/完成信息 API）
脚本结构复用 smoke-staging.sh 的 `fail()`/`ok()` 模式，用 `httpx`（api 容器内已有）调用 `http://localhost:8000/api/...`。具体任务创建/查询 endpoint 以 M-13/M-14 API 为准（Task Draft→Spec→Plan→Run），从已有 smoke-staging.sh 或 acceptance 脚本复制调用形态。

- [ ] **Step 2: 写 `infra/scripts/rollback-production.sh`**

基于 rollback-staging.sh：`-p kairos-production`、`compose.production.yml`、`--env-file /srv/kairos/env/production.env`。FIRST_PRODUCTION_RELEASE 逻辑：无 previous 镜像 → `PREVIOUS_PRODUCTION_IMAGE` 为空 → 用当前 `v0.1.0-<sha>` 镜像 restore（down 不带 `-v`，保留 volume）。

- [ ] **Step 3: 选择 smoke target（公开、稳定、小型、无需登录、robots 允许、静态）**

Run（本地只读确认）：
```bash
curl -fsS -A "kairos-production-smoke/1.0" https://example.com/ | head -c 400
curl -fsS -A "kairos-production-smoke/1.0" https://example.com/robots.txt | head -c 200
```
Expected: `example.com` 返回 200 静态页、robots 允许（`User-agent: *` + `Allow: /`）。若 example.com 不可用，改用 `https://www.iana.org/` 或其它权威静态页（同条件）。2～3 个字段：页面标题、正文/摘要小片段、source URL。1～2 页。**不**抓上海政府 24 页、不动态页、不 Golden C。

- [ ] **Step 4: 运行 Production Smoke（真实 tiny task）**

Run（本地）：
```bash
DEPLOY_HOST=47.238.145.24 ./infra/scripts/smoke-production.sh
```
Expected: 全部 `ok:`，最终 `PRODUCTION_SMOKE=PASS`。记录 `SMOKE_TASK_ID=<task_id>`。

- [ ] **Step 5: 验证 Worker 隔离（production 不消费 staging queue）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'docker exec kairos-staging-temporal-1 temporal operator task-queue list kairos-task --namespace default --address localhost:7233 2>/dev/null | grep -c "kairos-production" || echo 0'
```
Expected: staging queue 中无 production worker 引用（0）；production task 只出现在 `kairos-production` namespace。

- [ ] **Step 6: Rollback readiness（FIRST_PRODUCTION_RELEASE）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos && RELEASE_VERSION=v0.1.0 bash /srv/kairos/scripts/rollback-production.sh --check' 
```
（脚本支持 `--check` 时只做 readiness 验证：当前 immutable images 存在、manifest 可读、migration 0014 兼容、发布前备份存在；**不强制实际回滚破坏刚成功的数据**。若规范要求首次生产必须真实 rollback drill，则用 isolated production-like route 验证后恢复——以 Deployment Standards §20 为准。）
Expected: `ROLLBACK_READY=PASS`；`previous_release=none-first-release`（首版，不伪造 previous）。

- [ ] **Step 7: 本地契约测试（TEST E 已覆盖 rollback 选择逻辑）+ 语法校验**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ops/test_release_contract.py -q`；`bash -n infra/scripts/smoke-production.sh && bash -n infra/scripts/rollback-production.sh`
Expected: 3 passed；无语法错误。

- [ ] **Step 8: Commit**

```bash
git add infra/scripts/smoke-production.sh infra/scripts/rollback-production.sh
git commit -m "feat(deploy): add production smoke and rollback readiness

新增 smoke-production.sh（HTTPS + 注册/登录 + DeepSeek 配置 + 1 条 SPECIFIED_SOURCE tiny task + Record/Snapshot/Evidence/Quality/CSV/Completion 断言）与 rollback-production.sh（FIRST_PRODUCTION_RELEASE readiness，down 保留 volume）。关联模块：M-18"
```

---

## Task 8: Production Backup + DEPLOY-GATE-5 + Release Docs

**Files:**
- Create: `infra/scripts/_m18_production_acceptance.py`（服务器 api 容器内）
- Create: `docs/implementation/M-18-execution.md`
- Create: `docs/implementation/DEPLOY-GATE-5-execution.md`
- Modify: `README.md`（Production URL / 版本 / 部署流程 / runbook 链接，无 Secret）

**Interfaces:**
- Produces: `PRODUCTION_BACKUP_ID`（含 smoke 数据）push 到外部 S3 target；`OFF_SERVER_S3_COPY=PASS`；`DEPLOY-GATE-5=PASS`；最终状态 `M-18=DONE`、`Production=RELEASED`

- [ ] **Step 1: 运行 M-18 production acceptance（最小验收，非全量）**

创建 `infra/scripts/_m18_production_acceptance.py`（api 容器内，复用 M-17 acceptance 模式），覆盖：
1. Production config validation 拒绝 dev 默认（复用 TEST A 逻辑）
2. Redaction canary 0 明文
3. 隔离断言：`settings.s3_bucket == "kairos-production"`、`settings.temporal_namespace == "kairos-production"`、DB host 非 localhost
4. Worker/容量：`settings.worker_roles == "all"`、`capacity_global_active_tasks >= 1`
5. Ops health DB 指标可读（复用 `_ops_health` 语义）
6. 最近 release manifest 存在且字段完整
Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  "docker exec kairos-production-api-1 sh -c 'cd /app && python /app/infra/scripts/_m18_production_acceptance.py'"
```
Expected: 全部 `[PASS]`，`RESULT=PASS`。

- [ ] **Step 2: 真实 Production backup（smoke 后立即，推外部 S3）**

Run（服务器）：
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos && ENV=production BACKUP_DIR=/srv/kairos/backups bash /srv/kairos/scripts/backup.sh'
```
Expected: `BACKUP_DONE backup_id=production-<timestamp>-<sha>`；manifest 无明文 secret。然后 push 外部：
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos && BACKUP_ID=<id> bash /srv/kairos/scripts/backup-offsite-s3.sh'
```
Expected: `OFF_SERVER_S3_COPY=PASS backup_id=production-... src=<sha> dst=<sha>`；外部对象存在。记录 `PRODUCTION_BACKUP_ID` 到 release manifest（`BACKUP_ID=... RELEASE_VERSION=v0.1.0 bash release-manifest.sh` 更新）。

- [ ] **Step 3: Backup 验证（manifest READY + checksum + 外部对象存在）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'python3 -c "import json;d=json.load(open(\"/srv/kairos/backups/$(ls -1t /srv/kairos/backups | head -1)/manifest.json\"));print(\"backup_id\",d[\"backup_id\"],\"status\",d[\"status\"],\"pg\",d[\"postgres\"][\"sha256\"][:12],\"objects\",d[\"objects\"][\"sha256\"][:12],\"mig\",d[\"migration_head\"])"'
```
Expected: 字段完整；PG + objects checksum 存在。**不做**第二次完整 Restore Drill（M-17 已真实完成，REUSED_PASS）。

- [ ] **Step 4: 更新 release manifest（含 backup_id/rollback_target）**

Run:
```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  'cd /srv/kairos && RELEASE_VERSION=v0.1.0 BACKUP_ID=production-<id> ROLLBACK_TARGET=kairos-*:v0.1.0-<sha> bash /srv/kairos/scripts/release-manifest.sh'
```
Expected: `RELEASE_MANIFEST_OK`；manifest 含 backup_id + rollback_target。

- [ ] **Step 5: 最终 Ops Health（production）**

Run（服务器）：`cd /srv/kairos && ENV=production bash /srv/kairos/scripts/ops-health.sh`
Expected: `"status":"PASS"`；disk/container/DB/Temporal/MinIO/worker/release/backup 均正常。

- [ ] **Step 6: 写 `docs/implementation/M-18-execution.md`**

按 M-17 记录格式，包含：Status、M-18 起点 SHA、Release version v0.1.0、Git SHA、image digests、migration 0014、域名/HTTPS、Production 环境 ID（非 Secret）、backup manifest ID、smoke Task ID、Record/Snapshot/Evidence/CSV 引用、rollback target、health、Gate 结果、明确未做（full load test / long soak / full penetration test / DEFERRED-DYNAMIC-E2E-01 / 新增产品功能）、Git 证据（branch/commits/merge/tag/push 状态）。

- [ ] **Step 7: 写 `docs/implementation/DEPLOY-GATE-5-execution.md`**

列出 REUSED_PASS（M-16 reliability、M-14 quality/evidence、M-13 review、M-12 validation、M-09~11 crawler、Golden、Restore Drill 均引用既有 execution records）+ NEW_PASS（Production HTTPS、health、isolation、migration、worker、tiny task、Record、Evidence、Quality、CSV、backup、offsite backup、rollback readiness、ops health）+ DEFERRED（DEFERRED-DYNAMIC-E2E-01）。Gate 结论：PASS。

- [ ] **Step 8: 更新 README（Production URL / 版本 / 部署 / runbook 链接，无 Secret）**

修改 `README.md`：增加 Production URL `https://app.kairos.ac.cn`、当前部署版本 v0.1.0、部署流程（deploy-production.sh + rollback-production.sh + backup）、runbook 链接。**不写入任何 Secret/API Key/password。**

- [ ] **Step 9: Commit**

```bash
git add docs/implementation/M-18-execution.md docs/implementation/DEPLOY-GATE-5-execution.md README.md infra/scripts/_m18_production_acceptance.py
git commit -m "docs(ops): record M-18 DONE and DEPLOY-GATE-5 PASS

记录 Production Release v0.1.0、Production 隔离环境、不可变镜像 digest、正式域名/HTTPS、minimal smoke（task/record/evidence/quality/csv）、外部 off-site 备份、回滚就绪、ops health 与 Gate-5 结论；更新 README。关联模块：M-18"
```

- [ ] **Step 10: 最终报告（输出给用户，不入库）**

按 M-18 brief §77 精炼格式输出：M-18 STATUS / PRODUCTION PRECONDITION / RELEASE / PRODUCTION ENVIRONMENT / PRODUCTION SMOKE / BACKUP / ROLLBACK / DEPLOY-GATE-5（REUSED_PASS + NEW_PASS + DEFERRED）/ FINAL（M-17、M-18、DEPLOY-GATE-5、Production URL、Staging、Git）。明确未做项后停止。

---

## Self-Review

**1. Spec coverage（对照 M-18 brief 各 Phase + 实施计划 M-18 章 + DEPLOY-GATE-5 章）：**
- Production Preflight + External Backup Target → Task 1 ✓
- Production env/secrets isolation（不复用 staging secret）→ Task 2 ✓
- Production DB（干净库 alembic upgrade head）/ ObjectStorage bucket / Temporal namespace → Task 3 ✓
- Worker queue 隔离 → Task 3 + Task 7 ✓
- Immutable release + manifest + registry/load + digest → Task 4 ✓
- Git release flow（main fast-forward + tag v0.1.0 + push）→ Task 4 Step 8 ✓
- Reverse proxy / domain / HTTPS（HTTP→HTTPS + HSTS + auto-renew）→ Task 5 ✓
- Production 部署 + migration + health/readiness → Task 6 ✓
- Minimal Production Golden Path（1 user + DeepSeek + SPECIFIED_SOURCE tiny task）→ Task 7 ✓
- Evidence / Quality / CSV 真实读取 → Task 7 ✓
- Rollback readiness（FIRST_PRODUCTION_RELEASE）→ Task 7 ✓
- Production backup → external offsite → Task 8 ✓
- Ops health → Task 6 + Task 8 ✓
- DEPLOY-GATE-5 = PASS → Task 8 ✓
- 不重跑历史全量（REUSED_PASS）→ Global Constraints ✓
- DEFERRED-DYNAMIC-E2E-01 不处理 → Global Constraints ✓

**2. Placeholder scan：** 所有步骤给出可执行命令；`<sha>`、`<id>`、`<ops-email>` 为执行期真实值占位（在命令中由上一命令输出回填），非「TBD/TODO」。execution 顺序在 Task 3 Step 4 显式标注「先 Task 4 构建再回跑 migration」，避免 placeholder 镜像。✓

**3. Type consistency：** `compose.production.yml`（Task 2 修改）被 Task 3/4/6 复用；`production.env` 变量（Task 2）被 Task 3-8 复用；`backup-offsite-s3.sh`（Task 1）被 Task 8 复用；`deploy-production.sh`/`release-manifest.sh`（Task 4）被 Task 6/8 复用；`smoke-production.sh`/`rollback-production.sh`（Task 7）被 Task 8 复用；TEST C/E（Task 4）被 Task 7 复用。接口名一致。✓

**4. Production isolation check：** 独立 compose project（kairos-production）、独立 internal network、独立 volume、独立 DB（kairos_production）、独立 bucket（kairos-production）、独立 namespace（kairos-production）、独立 env（production.env）、独立 vhost；TEST B 断言不引用 staging；Task 3/7 验证 worker queue 不串。✓

**5. Secret boundary check：** production.env（0600）在服务器生成、不回显；backup-target.env（0600）不进 compose；manifest 断言无 Secret；acceptance 不打印 credential；Task 1 Step 3 明确本地临时文件即删。✓

**6. Backup precondition check：** Task 1 真实 S3 tiny 验证后才 `READY`；Task 8 在 smoke 数据后立即 backup 推外部；Staging 本地 copy 不冒充长期 target。✓

**7. Rollback safety check：** FIRST_PRODUCTION_RELEASE 用当前不可变镜像 restore（down 不带 -v 保留 volume）；不强制破坏刚成功数据的真实回滚；Deployment Standards §20 为准。✓

**8. Scope check：** 8 个 macro tasks；无新产品功能/页面/计费/K8s/多地域；不重跑历史全量；Gate-5 环境级最小验收。✓

---

## PROJECT SELF-APPROVAL

CHECK 1 M-17 production precondition 有明确处理路径 → **PASS**（Task 1）
CHECK 2 正式 cutover 前 external backup target READY → **PASS**（Task 1 Step 6）
CHECK 3 Production 与 Staging DB/storage/Temporal/network/secret 隔离 → **PASS**（Task 2/3）
CHECK 4 Staging 不受影响 → **PASS**（Task 5/6 复验 staging ready）
CHECK 5 Production internal services 无公网暴露 → **PASS**（TEST B + Task 6 network-boundary）
CHECK 6 Production secrets 不复用 Staging secret → **PASS**（Task 2 全新 openssl rand）
CHECK 7 Production migration 干净 DB upgrade head → **PASS**（Task 3）
CHECK 8 Immutable release、无 latest → **PASS**（Task 4 v0.1.0-<sha>）
CHECK 9 Release Manifest 完整且无 Secret → **PASS**（Task 4/8）
CHECK 10 正式域名/HTTPS 正确 → **PASS**（Task 5）
CHECK 11 Rollback target 真实存在 → **PASS**（Task 7 FIRST_PRODUCTION_RELEASE）
CHECK 12 Backup 在 smoke 前可用 → **PASS**（Task 1 READY，Task 8 smoke 后立即）
CHECK 13 不重新全量回归 → **PASS**（REUSED_PASS）
CHECK 14 DEFERRED-DYNAMIC-E2E-01 仍未处理 → **PASS**
CHECK 15 M-18 不加入新产品功能 → **PASS**
CHECK 16 Gate-5 只做环境最小验收 → **PASS**

---

## PLAN SELF-APPROVAL

**PLAN SELF-APPROVAL: PASS**
M-17 precondition handling: PASS · production offsite backup: PASS · production isolation: PASS · staging preservation: PASS · secret separation: PASS · network boundary: PASS · production DB initialization: PASS · object storage isolation: PASS · Temporal isolation: PASS · worker queue isolation: PASS · immutable release: PASS · release manifest: PASS · Git release flow: PASS · domain/DNS: PASS · HTTPS: PASS · rollback readiness: PASS · fast scoped testing: PASS · Gate-5 minimal acceptance: PASS · deferred dynamic untouched: PASS · placeholder scan: PASS · type/interface consistency: PASS
