# Kairos Staging 安全基线（Security Baseline）

状态：**PASS**（2026-08-12，M-17 只读审计）
服务器：47.238.145.24（Ubuntu 24.04.4 LTS，kernel 6.8.0-124-generic）
执行账号：`deploy`（uid=1002，组 `deploy,docker`；非 root）

> 审计方式：`infra/scripts/security-audit.sh`（只读，不修改服务器）。
> 审计输出见 `docs/operations/security-audit-raw.txt`。
> 本文档只记录结论与分类，绝不记录任何 Secret 值。

## 1. 公网端口

- 仅 `22 / 80 / 443` 在 `0.0.0.0` / `[::]` 监听。
- 内部服务（PostgreSQL 5432、Temporal 7233、MinIO 9000/9001、OTel 4317/4318、Worker 8000、Docker daemon 2375/2376）**均无公网监听**。
- Docker published ports：仅 `lumina-prod-nginx-1` 发布 `0.0.0.0:80/443`；kairos 业务容器全部只暴露容器内端口，无 host 映射。
- 结论：**PASS**

## 2. SSH

- `PasswordAuthentication no`（sshd_config）
- `PermitRootLogin no`（sshd_config）
- `PubkeyAuthentication yes`（默认）
- `MaxAuthTries 3`
- 日常运维账号 `deploy`；root 日常 SSH 不可用。
- 结论：**PASS**

## 3. 防火墙 / 暴力破解防护

- `ufw`：active。
- `firewalld`：inactive（未使用）。
- `fail2ban`：active。
- 云安全组 + 宿主机 ufw 双层边界，公网监听仅 22/80/443。
- 结论：**PASS**

## 4. Docker 安全

- Docker daemon 无 TCP socket 暴露（无 2375/2376）。
- 网络：
  - `kairos-staging-internal`：postgres / temporal / minio / otel / worker / api / web 所在私有网络。
  - `lumina-prod-internal`（edge）：api/web 通过 Docker DNS 被共享 nginx 反向代理访问，无公网端口。
- 全部 kairos 容器：`privileged=false`，无 `host` network，无 `/var/run/docker.sock` 挂载。
- 结论：**PASS**

## 5. Secrets 与文件权限

- `/srv/kairos/env/`：`drwx------ deploy:deploy`。
- `/srv/kairos/env/staging.env`：`600 deploy:deploy`。
- 备份脚本、restore 脚本、ops-health 脚本对 secret 只记录分类与引用，不输出明文。
- 结论：**PASS**

## 6. HTTPS / TLS

- `staging.kairos.ac.cn` 证书有效（expiry 2026-11-08，VALID 87 天），certbot 自动续期。
- HTTP → HTTPS 301 跳转（nginx vhost）。
- 安全响应头：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy`。
- SSE 反代配置 `proxy_buffering off` + 长 read timeout。
- 结论：**PASS**

## 7. Cookie / CORS

- Staging：`KAIROS_SESSION_COOKIE_SECURE=true`、`SameSite=lax`、`HttpOnly=true`（compose.base.yml）。
- `KAIROS_CORS_ORIGINS=["https://staging.kairos.ac.cn"]`，无 `*`、无任意 origin。
- 结论：**PASS**

## 8. sudo 最小权限

- `deploy` sudo 规则（`sudo -l`）：
  - `(root) NOPASSWD: /usr/bin/certbot`
  - `(root) NOPASSWD: /usr/bin/systemctl restart docker`
- 非 `NOPASSWD: ALL`；仅保留证书续期与 docker 重启所需的最小命令。
- 结论：**PASS**

## 9. 结论

所有基线项均 PASS，无需服务器配置修改。M-17 安全基线以本文档 + `security-audit.sh` / `check-network-boundary.sh` 作为持续可复用证据。

相关 Runbook：`docs/runbooks/security-baseline.md`
