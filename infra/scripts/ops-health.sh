#!/usr/bin/env bash
# ops-health：机器可读 P0/P1 健康判定。在服务器（deploy 用户）直接执行。
#
#   # 本机开发机调用（SSH 到服务器）：
#   DEPLOY_HOST=47.238.145.24 ./infra/scripts/ops-health.sh
#   # 在服务器上直接执行：
#   bash /srv/kairos/scripts/ops-health.sh
#
# 输出最后一行 JSON：{"status":"PASS|P1|P0","checks":{...}}
# 退出码：0=PASS 1=P1 2=P0
set -euo pipefail

# 支持从开发机 SSH 到服务器执行
if [ -n "${DEPLOY_HOST:-}" ]; then
  DEPLOY_USER="${DEPLOY_USER:-deploy}"
  SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
  exec ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    "${DEPLOY_USER}@${DEPLOY_HOST}" 'bash -s' < "$0"
fi

SCRIPTS_DIR="${SCRIPTS_DIR:-/srv/kairos/scripts}"
BACKUP_DIR="${BACKUP_DIR:-/srv/kairos/backups}"
# 环境隔离：staging 用固定 container_name（kairos-api/kairos-web），
# production 无 container_name → compose 派生名 kairos-production-{api,worker,web}-1。
ENV_NAME="${ENV_NAME:-staging}"
if [ "$ENV_NAME" = "production" ]; then
  API_CONTAINER="${API_CONTAINER:-kairos-production-api-1}"
  WORKER_CONTAINER="${WORKER_CONTAINER:-kairos-production-worker-1}"
  WEB_CONTAINER="${WEB_CONTAINER:-kairos-production-web-1}"
  PG_VOL="kairos-production_postgres_data"
  MINIO_VOL="kairos-production_minio_data"
else
  API_CONTAINER="${API_CONTAINER:-kairos-api}"
  WORKER_CONTAINER="${WORKER_CONTAINER:-kairos-staging-worker-1}"
  WEB_CONTAINER="${WEB_CONTAINER:-kairos-web}"
  PG_VOL="kairos-staging_postgres_data"
  MINIO_VOL="kairos-staging_minio_data"
fi

status=PASS
declare -A checks=()

fail_fast_status() {  # 只会升级，不会降级：PASS < P1 < P0
  local s="$1"
  if [ "$s" = "P0" ]; then status=P0
  elif [ "$s" = "P1" ] && [ "$status" != "P0" ]; then status=P1
  fi
}

# --- API liveness / readiness（docker exec 容器内自检，api 镜像无 curl → 用 python urllib）---
if docker exec "$API_CONTAINER" python -c \
  "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health/live', timeout=5).status==200 else 1)" >/dev/null 2>&1; then
  checks[api_live]=ok
else
  checks[api_live]=down; fail_fast_status P0
fi
ready="$(docker exec "$API_CONTAINER" python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/health/ready', timeout=8).read().decode())" 2>/dev/null || echo '{"status":"error"}')"
if printf '%s' "$ready" | grep -q '"status":"ok"'; then
  checks[api_ready]=ok
else
  checks[api_ready]=degraded; fail_fast_status P0
fi

# --- 业务容器状态 + restart loop ---
for c in "$API_CONTAINER" "$WORKER_CONTAINER" "$WEB_CONTAINER"; do
  st="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
  checks["container_$c"]=$st
  if [ "$st" != "running" ]; then fail_fast_status P0; fi
done
for c in "$API_CONTAINER" "$WORKER_CONTAINER"; do
  r="$(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || echo 0)"
  if [ "$r" -gt 5 ]; then checks["restart_loop_$c"]=$r; fail_fast_status P1; fi
done

# --- 磁盘：根 / Docker data-root / PG / MinIO / backups ---
for spec in "root:/" "docker:/var/lib/docker" \
  "pg:/var/lib/docker/volumes/$PG_VOL" \
  "minio:/var/lib/docker/volumes/$MINIO_VOL" \
  "backups:$BACKUP_DIR"; do
  name="${spec%%:*}"; path="${spec#*:}"
  df_line="$(df -P "$path" 2>/dev/null | tail -1)" || continue
  pct="$(printf '%s' "$df_line" | awk '{print $5}' | tr -d '%')"
  checks["disk_$name"]="${pct}%"
  if [ "$pct" -ge 90 ]; then fail_fast_status P1; fi
done

# --- DB / 业务指标（api 容器内）---
db_metrics='{}'
if [ -f "$SCRIPTS_DIR/_ops_health.py" ]; then
  docker cp "$SCRIPTS_DIR/_ops_health.py" "$API_CONTAINER":/app/_ops_health.py >/dev/null 2>&1 || true
  db_metrics="$(docker exec "$API_CONTAINER" python /app/_ops_health.py 2>/dev/null || echo '{}')"
fi

# --- 最近备份存在性 ---
latest="$(ls -1t "$BACKUP_DIR"/*/manifest.json 2>/dev/null | head -1 || true)"
checks[latest_backup]="${latest:-none}"
if [ -z "$latest" ]; then fail_fast_status P1; fi

# 合并 DB 指标到输出
printf '{"status":"%s","checks":{' "$status"
first=1
for k in "${!checks[@]}"; do
  [ "$first" = "0" ] && printf ','
  printf '"%s":"%s"' "$k" "${checks[$k]}"
  first=0
done
printf '},'
printf '"db_metrics":%s' "$db_metrics"
printf '}\n'

case "$status" in
  P0) exit 2 ;;
  P1) exit 1 ;;
  *) exit 0 ;;
esac
