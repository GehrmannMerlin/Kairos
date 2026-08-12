#!/usr/bin/env bash
# kairos-production minimal golden path smoke.
#
# Checks (brief §57): public HTTPS ready + internal health + one tiny real task
# (register/login -> DeepSeek config -> SPECIFIED_SOURCE task -> understand -> spec-confirm ->
# plan -> workflow -> records/snapshot/evidence/quality/completion/csv). Search NOT required.
#
# Usage (from repository root):
#   ./infra/scripts/smoke-production.sh
#
# Prerequisites (on the server, 0600, never echoed):
#   /srv/kairos/env/smoke-deepseek.key   # DeepSeek API key for the smoke model config
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${DEPLOY_HOST:-47.238.145.24}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")
COMPOSE_DIR="/srv/kairos/compose"
COMPOSE="docker compose -p kairos-production -f ${COMPOSE_DIR}/compose.base.yml -f ${COMPOSE_DIR}/compose.production.yml"
DOMAIN="${PROD_DOMAIN:-app.kairos.ac.cn}"
KEY_FILE="/srv/kairos/env/smoke-deepseek.key"

fail() { printf 'SMOKE FAIL: %s\n' "$*" >&2; exit 1; }
ok() { printf '  ok: %s\n' "$*"; }

echo "==> [1] public HTTPS ready (${DOMAIN})"
curl -fsS -m 15 "https://${DOMAIN}/api/health/ready" >/dev/null 2>&1 \
  && ok "public HTTPS ready" || fail "public HTTPS ready failed"
curl -fsS -I -m 15 "http://${DOMAIN}/api/health/ready" 2>/dev/null | grep -qi "^HTTP/.* 301" \
  && ok "HTTP->HTTPS redirect" || fail "HTTP->HTTPS redirect missing"

echo "==> [2] internal health live/ready"
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE} exec -T api python - <<'PY'
import urllib.request
for path in ('/api/health/live','/api/health/ready'):
    r=urllib.request.urlopen('http://localhost:8000'+path, timeout=15)
    print(path, r.status, r.read().decode()[:160])
PY" || fail "internal health"

echo "==> [3] production minimal golden path (tiny SPECIFIED_SOURCE task)"
# 服务器侧先确认 key 文件存在且 0600（值不回显）
"${SSH[@]}" "[ -f ${KEY_FILE} ] && stat -c '%a' ${KEY_FILE} | grep -qE '^600$' \
  || { echo 'smoke-deepseek.key missing or not 0600'; exit 1; }" || fail "DeepSeek key file"
# 把驱动脚本经 stdin 注入 api 容器 /tmp（图片不含 infra/，运行时可注入）
"${SSH[@]}" "cd ${COMPOSE_DIR} && ${COMPOSE} exec -T api sh -c 'cat > /tmp/_m18_production_smoke.py'" \
  < "$ROOT/infra/scripts/_m18_production_smoke.py" || fail "inject smoke driver"
# 在服务器端展开 key 值，经 -e 传入容器；值不出现在本地 argv / 终端输出
"${SSH[@]}" "cd ${COMPOSE_DIR} && KEY=\"\$(cat ${KEY_FILE})\" && \
    ${COMPOSE} exec -T -e PROD_SMOKE_DEEPSEEK_KEY=\"\$KEY\" api \
    sh -c 'python /tmp/_m18_production_smoke.py'" || fail "production golden path"

echo
echo "SMOKE PASS (production minimal golden path)"
