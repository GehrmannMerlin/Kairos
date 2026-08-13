# Kairos Security Baseline Runbook

> M-17 安全基线操作手册。记录当前允许的公网端口、SSH 配置、防火墙、私有服务、
> secret 位置分类、HTTPS/TLS、Cookie/CORS 与 Docker 限制，以及复核命令。
> 实际审计事实见 `docs/operations/security-baseline.md`（服务器 47.238.145.24）。

## 1. Allowed Public Ports

仅 `22 / 80 / 443`。除此之外（5432 PostgreSQL、7233 Temporal、9000/9001 MinIO、
4317/4318 OTel、8000 Worker/API、2375/2376 Docker daemon）一律禁止公网。

复核（只读）：
```bash
ssh deploy@47.238.145.24 'ss -tln | grep -Ev "^State.*Local" | awk "{print \$4}" | grep -vE "^127\.|^::1"'
# 期望只出现 *:22 / *:80 / *:443
DEPLOY_HOST=47.238.145.24 bash infra/scripts/check-network-boundary.sh   # 返回 PASS
```

## 2. SSH

- `PasswordAuthentication no`
- `PermitRootLogin no`
- `PubkeyAuthentication yes`
- `MaxAuthTries 3`
- 日常运维账号 `deploy`（uid 1002，组 docker）；root 日常 SSH 不可用。
- sshd 修改纪律：`sudo sshd -t` → 保留已连接 session → 二次连接验证 → 才 `sudo systemctl reload ssh`。

复核：
```bash
ssh deploy@47.238.145.24 'sudo cat /etc/ssh/sshd_config | grep -Ei "PasswordAuthentication|PermitRootLogin|MaxAuthTries"'
```

## 3. Firewall / 暴力破解防护

- `ufw` active；`firewalld` 未用。
- `fail2ban` active。
- 云安全组 + 宿主机 ufw 双层。

复核：
```bash
ssh deploy@47.238.145.24 'systemctl is-active ufw fail2ban'
```

## 4. Private Services（Docker 网络）

- `kairos-staging-internal`：postgres / temporal / minio / otel / worker / api / web。
- `lumina-prod-internal`（edge）：api/web 仅被共享 nginx 经 Docker DNS 反代，无公网端口。
- 全部 kairos 容器 `privileged=false`、无 host network、无 `/var/run/docker.sock` 挂载。

复核：
```bash
ssh deploy@47.238.145.24 'docker ps --format "{{.Names}} | {{.Ports}}"'
# 期望只有 lumina-prod-nginx-1 显示 0.0.0.0:80/443
```

## 5. Secret Locations（分类，无值）

| Secret | 位置 | 权限 | 轮换 |
|---|---|---|---|
| DB password / MinIO secret / session secret / credential master key | `/srv/kairos/env/staging.env` | 600 deploy | 变更时 |
| Backup encryption key | `/srv/kairos/env/backup.key` | 600 deploy | 变更时 |
| SSH private key | `~/.ssh/kairos_staging_deploy_rsa`（本机） | 600 | 疑似泄露即轮换 |
| Provider API keys | 数据库密文（envelope encryption，主密钥在 env） | — | 按 M-03 |

规则：secret 不进 Git、不进镜像、不进日志/OTel/Temporal history/backup manifest。
focused scan：
```bash
bash infra/scripts/secret-scan.sh    # 期望 SECRET_SCAN_RESULT: PASS
```

## 6. HTTPS / TLS

- `staging.kairos.ac.cn` 证书 certbot 自动续期；HTTP → HTTPS 301。
- 安全响应头：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy`（+ Production HSTS）。
- SSE：`proxy_buffering off` + 长 read timeout（3600s）。
- Production 模板：`infra/reverse-proxy/zz-kairos-production-tls.conf`（M-18 启用）。

复核：
```bash
curl -fsS https://staging.kairos.ac.cn/api/health/ready
echo | openssl s_client -connect staging.kairos.ac.cn:443 2>/dev/null | openssl x509 -noout -dates
```

## 7. Cookie / CORS

- Staging：`KAIROS_SESSION_COOKIE_SECURE=true`、`SameSite=lax`、`HttpOnly=true`。
- `KAIROS_CORS_ORIGINS=["https://staging.kairos.ac.cn"]`；无 `*`。
- Production 模板要求 `Secure cookie` + 正式域名 origin（`KAIROS_CORS_ORIGINS=["https://app.kairos.ac.cn"]`）。
- production 配置违规由 `Settings.validate_runtime()` 启动即失败（M-17）。

## 8. Docker Restrictions

- 业务容器不挂载 docker.sock、不 privileged、不 host network/pid。
- 镜像不可变 tag（staging-<sha>），无 `latest` 作为唯一可追溯标识。

复核：
```bash
ssh deploy@47.238.145.24 'docker ps -q | xargs -I{} docker inspect -f "{{.Name}} priv={{.HostConfig.Privileged}} sock={{range .Mounts}}{{if eq .Source \"/var/run/docker.sock\"}}DOCKER_SOCK{{end}}{{end}}" {}'
```
