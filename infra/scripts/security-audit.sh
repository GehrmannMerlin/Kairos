#!/usr/bin/env bash
# 只读服务器安全审计。SSH 到目标，输出审计事实，绝不修改配置。
# 用法：DEPLOY_HOST=47.238.145.24 ./infra/scripts/security-audit.sh
set -euo pipefail
DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST required}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/kairos_staging_deploy_rsa}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${DEPLOY_USER}@${DEPLOY_HOST}")

"${SSH[@]}" 'bash -s' <<'EOF'
set -uo pipefail
section() { echo; echo "===== $1 ====="; }
section "OS / kernel"
. /etc/os-release >/dev/null 2>&1 && echo "$PRETTY_NAME" || head -3 /etc/os-release
uname -r
section "current user (should be deploy, not root)"
whoami; id
section "sshd effective config"
sshd -T 2>/dev/null | grep -Ei "^(passwordauthentication|permitrootlogin|pubkeyauthentication|maxauthtries|port) " || true
section "listening ports (non-loopback listeners)"
ss -tln 2>/dev/null | awk 'NR==1 || ($4 !~ /^127\./ && $4 !~ /^::1/)'
section "firewall (ufw / firewalld / nft / iptables)"
systemctl is-active ufw 2>/dev/null || true
systemctl is-active firewalld 2>/dev/null || true
sudo -n nft list ruleset 2>/dev/null | head -30 || true
sudo -n iptables -S 2>/dev/null | head -30 || true
section "fail2ban"
systemctl is-active fail2ban 2>/dev/null || true
sudo -n fail2ban-client status 2>/dev/null | head -15 || true
section "docker daemon TCP exposure"
ss -tln 2>/dev/null | grep -E ":(2375|2376)\b" || echo "no docker TCP socket"
section "docker networks"
docker network ls
section "containers + published ports"
docker ps --format '{{.Names}} | {{.Image}} | {{.Ports}}' | head -50
section "srv/kairos env permissions"
ls -la /srv/kairos/env/ 2>/dev/null || true
stat -c '%a %U:%G %n' /srv/kairos/env/*.env 2>/dev/null || true
section "certificate status (staging)"
sudo -n certbot certificates 2>/dev/null | grep -E "Certificate Name|Domains|Expiry Date" || true
section "docker.sock / privileged / host network"
docker ps -q | xargs -I{} docker inspect -f '{{.Name}} privileged={{.HostConfig.Privileged}} net={{.HostConfig.NetworkMode}} sock={{range .Mounts}}{{if eq .Source "/var/run/docker.sock"}}DOCKER_SOCK{{end}}{{end}}' {} 2>/dev/null || true
section "deploy sudo rules"
sudo -n -l 2>/dev/null | head -20 || true
EOF
