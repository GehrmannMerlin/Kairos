#!/usr/bin/env bash
# 断言网络边界：仅允许 22/80/443 公网监听；内部服务不得发布公网端口。
# 返回 0=PASS 1=FAIL。供 M-17 TEST G 与 staging acceptance 复用。
set -euo pipefail
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")

fail=0

# 1) 非 loopback 监听端口只允许 22/80/443
listeners="$("${SSH[@]}" 'ss -tln 2>/dev/null | awk "NR>1 && \$4 !~ /^127\./ && \$4 !~ /^::1/ {print \$4}"')"
for line in $listeners; do
  port="${line##*:}"
  case "$port" in
    22|80|443) ;;
    *)
      echo "PUBLIC-UNEXPECTED-PORT:$port"
      fail=1
      ;;
  esac
done

# 2) docker published ports 不允许 0.0.0.0 / [::] 上的内部端口
published="$("${SSH[@]}" 'docker ps --format "{{.Ports}}"' \
  | grep -Eo "(0\.0\.0\.0|\[::\]|::):[0-9]+" \
  | sed -E "s/.*://" | sort -un || true)"
for port in $published; do
  case "$port" in
    22|80|443) ;;
    *)
      echo "PUBLISHED-INTERNAL-PORT:$port"
      fail=1
      ;;
  esac
done

if [ "$fail" -eq 0 ]; then
  echo "NETWORK_BOUNDARY: PASS"
  exit 0
fi
echo "NETWORK_BOUNDARY: FAIL"
exit 1
