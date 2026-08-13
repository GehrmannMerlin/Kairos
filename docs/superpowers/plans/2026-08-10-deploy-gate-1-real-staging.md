# DEPLOY-GATE-1 真实服务器 Staging 上线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在真实服务器 `47.238.145.24` 上把 M-01～M-04 系统以 `kairos-staging` 独立 Compose 项目上线到 `https://staging.kairos.ac.cn`，完成 Migration / Health / Auth / Ownership / Credential / Temporal / M-04 Checkpoint / Persistence / Restart / Rollback Readiness 全部 Gate Smoke，达成 `DEPLOY-GATE-1 = PASS`、`M-04 = DEPLOYED`、`M-05 = UNBLOCKED`（本轮不开始 M-05）。

**Architecture:** 目标服务器 **并非空机**，是共享生产服务器（lumina / stellaris / aurora-preview 三套线上业务）。80/443 已被共享 nginx（`lumina-prod-nginx-1`）占用，且该 nginx 采用「每个项目 vhost `.conf` 通过 compose override 以只读 bind-mount 注入容器」的既有模式（aurora/stellaris 同款）。因此 kairos-staging **不新增第二套反向代理**，而是复用共享 nginx：新建 `zz-kairos-staging-tls.conf` vhost 指向 kairos 的 web/api 容器（Docker DNS 解析），TLS 证书由宿主机 certbot 签发。kairos 容器全部走独立 `kairos-staging` 私有网络，仅 web/api 额外 join 共享的 `lumina-prod-internal` 网络供反向代理访问；PostgreSQL / Temporal / MinIO / OTel 不暴露任何宿主端口。镜像采用「本地 buildx 构建不可变镜像（`kairos-<svc>:staging-<gitsha>`）→ `docker save` → SSH → `docker load`」的 DEPLOY-GATE-1 bootstrap transport，不在服务器现场构建源码。

**Tech Stack:** Docker 29.6.2 + Compose v5.3.1（服务器已有）；Ubuntu 24.04 / x86_64；本地 Docker Desktop buildx（linux/amd64，与服务器同架构）；nginx（共享反向代理，沿用现有）；certbot（宿主机 /etc/letsencrypt）；alembic head 0004；项目后端 python:3.11-slim、前端 node:24-alpine → nginx:1.27-alpine prod。

## Global Constraints

- **真实值硬编码，禁止占位符**：IP `47.238.145.24`、staging 入口 `staging.kairos.ac.cn`、A 记录 `staging → 47.238.145.24`。Production 保留 `app.kairos.ac.cn`，本轮**不部署**。
- **不破坏共享生产**：不 kill 现有容器、不删未知 volumes/images/data、不覆盖 aurora/stellaris vhost、不重置防火墙、不在 lumina compose 里硬改。新 vhost 以独立 override 注入，并保留 aurora/stellaris 既有挂载。
- **SSH 安全顺序**：先装 `deploy` 用户 + 新 key 并验证新会话 → 备份 sshd_config → 再加固（PubkeyAuthentication yes / PasswordAuthentication no / PermitRootLogin no）→ `sshd -t` → reload → 新会话复验 → 才收尾。禁止未验证先关闭把自己锁在门外。服务器现有 `PasswordAuthentication no` 已是基线，`PermitRootLogin yes` 需谨慎关闭（先确认无线上服务依赖 root SSH，变更前备份，变更后以 deploy + ecs-user 双验证）。
- **Secret 纪律**：SSH 私钥只存 `~/.ssh/kairos_staging_deploy_rsa`（repo 外）；staging secrets（DB 密码 / session secret / credential master key / MinIO secret）只生成并写入服务器 `/srv/kairos/env/staging.env`（umask 077），不进 Git、不进日志、不进 execution record；Master Key（M-03）**首次生成后跨 redeploy 保持稳定**。
- **镜像纪律**：不可变 tag 含 Git SHA；禁止 server 端 `git pull / npm build / pip install / docker build`。
- **公网边界**：仅 22/80/443 对外；PostgreSQL/Temporal/MinIO/OTel/Worker 内部端口不暴露公网。
- **Migration**：staging 用干净 DB，`alembic upgrade head` 达 0004；禁止手工建表。
- **测试范围**：本轮只跑真实 Staging Smoke（HTTPS/health/auth/ownership/credential/Temporal/M-04 checkpoint/persistence/secret scan）。不重跑 131 项本地回归 / 47 provider / 9 credential / browser E2E / 压测。Repository 侧只跑 `docker compose config` 与脚本 syntax 检查。
- **Git**：分支 `ci/deploy-gate-1-staging`（从 M-04 HEAD 起），本地 2～5 个 Commit，全部带中文正文；**不 push / 不 merge / 不 tag / 不部署 Production**。部署身份用本地 Git SHA。
- **DNS**：无 Alibaba CLI / 凭据 / browser automation 授权 → 预计 `BLOCKED_DNS_AUTH`。届时只输出唯一所需动作：在 kairos.ac.cn DNS Zone 创建 `staging A 47.238.145.24`。其余全部服务器工作照常完成并用内部端点验证。
- **资源保护**：磁盘当前 93%（7.5G 可用），全流程监控磁盘；transfer 后清理本机/服务器 tar；绝不对共享服务器执行 `docker system prune -a`。
- 不实现 M-05+；不新增收费/RBAC/后台系统。

---

## 术语与共享接口（跨 Task 一致）

**服务器 SSH 访问（已探明可用）：**
- `ssh lumina-prod` / `ssh stellaris-server`（均为 `ecs-user@47.238.145.24`，Passwordless sudo，docker 组）。
- 新部署用户 `deploy`；新 key `~/.ssh/kairos_staging_deploy_rsa`（RSA 4096，comment `kairos-staging-deploy`）。

**共享 nginx 注入点：**
- 容器 `lumina-prod-nginx-1`，网络 `lumina-prod-internal`，`ReadonlyRootfs=false`。
- 既有挂载：`/opt/aurora-preview/deploy/nginx/conf.d/aurora-tls.conf → /etc/nginx/conf.d/zz-aurora-tls.conf`、`/opt/stellaris/deploy/nginx/conf.d/zz-stellaris-tls.conf → /etc/nginx/conf.d/zz-stellaris-tls.conf`、`/var/www/certbot`、`/etc/letsencrypt`。
- kairos 新增挂载：`/srv/kairos/deploy/nginx/conf.d/zz-kairos-staging-tls.conf → /etc/nginx/conf.d/zz-kairos-staging-tls.conf`。
- Docker DNS 上游名：`kairos-web:80`、`kairos-api:8000`。

**Staging 环境常量（服务器 /srv/kairos）：**
- Compose project：`kairos-staging`
- 私有网络：`kairos-staging-internal`
- 目录：`/srv/kairos/{compose,env,data/staging,backups/staging,scripts,releases,deploy/nginx/conf.d}`
- env 文件：`/srv/kairos/env/staging.env`（权限 600）
- 镜像 tag：`kairos-web:staging-<gitsha>` / `kairos-api:staging-<gitsha>` / `kairos-worker:staging-<gitsha>`

**Git 基线：**
- M-04 local = DONE（`docs/implementation/M-04-execution.md`，alembic 0004）。
- 新分支 `ci/deploy-gate-1-staging` 从 M-04 集成基线创建；记录 baseline SHA。

---

## Task 1: 真实外部环境 Preflight 与基线固化

**Files:**
- Modify: `docs/implementation/DEPLOY-GATE-1-execution.md`（创建，先写 Status 为 `IN_PROGRESS`，后续任务回填证据）

**Interfaces:**
- Consumes: 用户提供的真实 IP `47.238.145.24`、域名 `kairos.ac.cn`、staging 主机 `staging.kairos.ac.cn`。
- Produces: 固化基线 SHA、分支名、服务器审计结论、DNS 现状、共享 nginx 拓扑结论，供 Task 2～8 引用。

- [ ] **Step 1: 固化 Git 基线**

```bash
git rev-parse HEAD          # 记录 M-04 集成基线 SHA
git status
git branch --show-current
git log --oneline -8
```

预期：工作树干净；记录 baseline SHA；创建 `ci/deploy-gate-1-staging` 分支：

```bash
git switch -c ci/deploy-gate-1-staging
git rev-parse HEAD          # 记入 execution record
```

- [ ] **Step 2: 固化服务器与网络事实**

```bash
# 从本地执行（只读探测，不输出私钥）
ssh -o BatchMode=yes lumina-prod 'whoami; uname -a; cat /etc/os-release | head -3; nproc; free -h | head -2; df -h / | tail -1'
ssh -o BatchMode=yes lumina-prod 'docker version --format "{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}"; docker compose version'
```

预期记录：Ubuntu 24.04.4 / x86_64 / 8 CPU / 14Gi RAM / 磁盘 93%（7.5G 可用）/ Docker 29.6.2 + Compose v5.3.1。

- [ ] **Step 3: 固化 DNS 现状**

```bash
nslookup staging.kairos.ac.cn 223.5.5.5
nslookup kairos.ac.cn 223.5.5.5
# 权威 NS 查询
curl -s -m 8 -H 'accept: application/dns-json' "https://dns.alidns.com/resolve?name=kairos.ac.cn&type=NS&cd=1"
curl -s -m 8 -H 'accept: application/dns-json' "https://dns.alidns.com/resolve?name=staging.kairos.ac.cn&type=A&cd=1"
```

预期记录：zone 存在（NS = dns11/dns12.hichina.com，Alibaba DNS），`staging.kairos.ac.cn` **无 A 记录**；本机/服务器无 aliyun CLI 与凭据 → DNS control-plane 不可自动 → 记为 `BLOCKED_DNS_AUTH` 候选。

- [ ] **Step 4: 固化共享服务器拓扑结论**

记录（已探明）：服务器托管 lumina / stellaris / aurora-preview 三套线上业务；共享 nginx `lumina-prod-nginx-1` 独占 80/443；既有 vhost 注入方式为 compose override bind-mount；`ReadonlyRootfs=false`。写为 execution record 的「服务器审计」章节，作为 Task 5/6 不破坏共享业务的依据。

- [ ] **Step 5: Commit 基线**

```bash
git add docs/implementation/DEPLOY-GATE-1-execution.md
git commit -m "docs(deploy): record DEPLOY-GATE-1 baseline and server audit

记录真实服务器 47.238.145.24 与 staging.kairos.ac.cn 的基线 SHA、共享 nginx 拓扑、
DNS 现状（zone 在 Alibaba DNS 但无 A 记录，无 control-plane 授权）与服务器资源审计。
关联模块：DEPLOY-GATE-1"
```

预期：commit 成功；baseline 与事实写入 execution record。

---

## Task 2: SSH Deploy Key + deploy 用户 + SSH 安全加固

**Files:**
- Create（repo 外）: `~/.ssh/kairos_staging_deploy_rsa` / `.pub`（RSA 4096，comment `kairos-staging-deploy`）
- Modify（服务器）: `/etc/ssh/sshd_config`（先备份 `sshd_config.kairos.bak-YYYYMMDD`）
- Modify（服务器）: `/etc/ssh/sshd_config.d/` 或主配置（`PermitRootLogin`、`MaxAuthTries`）
- Create（服务器）: `/home/deploy/.ssh/authorized_keys`

**Interfaces:**
- Consumes: Task 1 探明的 `ecs-user` 现有 SSH 通道（`lumina-prod` / `stellaris-server`）。
- Produces: `deploy` 用户 + 新 key 的可用会话；SSH key-only + 禁 root + Fail2ban；public-key fingerprint（可写入 execution record，私钥绝不输出）。

- [ ] **Step 1: 生成专用 Deploy Key（本地）**

```bash
ls -la ~/.ssh/kairos_staging_deploy_rsa 2>/dev/null || \
ssh-keygen -t rsa -b 4096 -C "kairos-staging-deploy" -N "" -f ~/.ssh/kairos_staging_deploy_rsa
chmod 600 ~/.ssh/kairos_staging_deploy_rsa
ssh-keygen -lf ~/.ssh/kairos_staging_deploy_rsa.pub   # 记录 fingerprint
```

约束：只打印 fingerprint，绝不 `cat` 私钥；key 在 repo 外；`.gitignore` 已有 `*.key` 等兜底。

- [ ] **Step 2: 在服务器创建 deploy 用户并安装公钥（Method 1，使用现有 ecs-user 通道）**

```bash
PUB="$(cat ~/.ssh/kairos_staging_deploy_rsa.pub)"
ssh -o BatchMode=yes stellaris-server "sudo useradd -m -s /bin/bash deploy \
  && sudo mkdir -p /home/deploy/.ssh \
  && echo '$PUB' | sudo tee /home/deploy/.ssh/authorized_keys >/dev/null \
  && sudo chown -R deploy:deploy /home/deploy/.ssh \
  && sudo chmod 700 /home/deploy/.ssh && sudo chmod 600 /home/deploy/.ssh/authorized_keys \
  && sudo usermod -aG docker deploy \
  && sudo -n -u deploy true && echo DEPLOY_USER_CREATED"
```

约束：追加式安装公钥（不替换已有 key）；`deploy` 加入 docker 组用于 compose 部署；最小 sudo（仅后续脚本需要的明确命令，通过 sudoers.d 单独文件授予，不授 `ALL`）。

- [ ] **Step 3: 用新 Key 验证第二会话（成功后才继续加固）**

```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa -o BatchMode=yes -o StrictHostKeyChecking=accept-new deploy@47.238.145.24 'whoami && id'
```

预期输出 `deploy`；失败则停止加固并排查。

- [ ] **Step 4: 备份并加固 sshd（安全顺序）**

```bash
ssh -o BatchMode=yes stellaris-server 'sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.kairos.bak-$(date +%Y%m%d-%H%M%S)'
# 确认当前 PermitRootLogin 状态与无线上服务依赖 root SSH 后：
ssh -o BatchMode=yes stellaris-server 'sudo sed -i "s/^#\?PermitRootLogin.*/PermitRootLogin no/" /etc/ssh/sshd_config \
  && grep -q "^MaxAuthTries" /etc/ssh/sshd_config || echo "MaxAuthTries 3" | sudo tee -a /etc/ssh/sshd_config'
ssh -o BatchMode=yes stellaris-server 'sudo sshd -t && echo SSHD_SYNTAX_OK'
ssh -o BatchMode=yes stellaris-server 'sudo systemctl reload sshd && echo SSHD_RELOADED'
```

预期：`sshd -t` PASS；reload 成功；`PasswordAuthentication no` 已为基线（若未生效则同样置 no，先复验 key 会话）。

- [ ] **Step 5: 加固后复验新会话 + 现有 ecs-user 通道**

```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa -o BatchMode=yes deploy@47.238.145.24 'echo DEPLOY_OK'
ssh -o BatchMode=yes stellaris-server 'echo ECSUSER_OK'
```

预期两者均成功，证明未锁死任何入口。

- [ ] **Step 6: 安装 Fail2ban（服务器当前发行版等价防暴力破解）**

```bash
ssh -o BatchMode=yes stellaris-server 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban'
ssh -o BatchMode=yes stellaris-server 'sudo systemctl enable --now fail2ban && sudo systemctl is-active fail2ban'
```

预期：fail2ban active。22 保持公网开放（本项目已确认部署决定），仅 key 认证。

- [ ] **Step 7: 记录证据**

execution record 追加：deploy user 创建成功、新会话验证成功、`PermitRootLogin no` + `PasswordAuthentication no` + Fail2ban active、public-key fingerprint。私钥信息不落盘。

---

## Task 3: 服务器基线 / /srv/kairos 目录 / Staging Secrets 一次性生成

**Files:**
- Create（服务器）: `/srv/kairos/{compose,env,data/staging,backups/staging,scripts,releases,deploy/nginx/conf.d}`
- Create（服务器）: `/srv/kairos/env/staging.env`（umask 077，权限 600）
- Create（服务器）: `/etc/systemd/system/docker-compose-kairos-staging.service`（重启自恢复辅助，见 Task 6）

**Interfaces:**
- Consumes: Task 2 的 `deploy` 用户；Docker 已存在（版本已验证）。
- Produces: 稳定的 staging secret 集合（幂等：env 已存在则保留，首次生成后跨 redeploy 不重建 Master Key）。

- [ ] **Step 1: 校验/配置服务器基础（不无意义重装 Docker）**

```bash
ssh -o BatchMode=yes stellaris-server 'docker version --format "{{.Server.Version}}"; docker compose version'
ssh -o BatchMode=yes stellaris-server 'timedatectl | head -4; sudo timedatectl set-timezone Asia/Shanghai 2>/dev/null || true'
# Docker 日志轮转：确认/新增 daemon.json log-opts
ssh -o BatchMode=yes stellaris-server 'test -f /etc/docker/daemon.json && sudo cat /etc/docker/daemon.json || echo "{}"'
```

预期：Docker 29.6.2 / Compose v5.3.1 已在，不重装；时区确认；日志轮转若缺失则追加 `log-opts: max-size 20m, max-file 3`。

- [ ] **Step 2: 创建目录结构（owner deploy，严格权限）**

```bash
ssh -o BatchMode=yes stellaris-server 'sudo mkdir -p /srv/kairos/{compose,env,data/staging,backups/staging,scripts,releases,deploy/nginx/conf.d} \
  && sudo chown -R deploy:deploy /srv/kairos && sudo chmod 700 /srv/kairos/env'
```

预期：`/srv/kairos` 就绪，env 目录仅 deploy 可访问。

- [ ] **Step 3: 幂等生成 Staging Secrets（仅实际需要的）**

服务器端执行（`umask 077`），首次生成、已存在则保留：

```bash
ssh -o BatchMode=yes stellaris-server 'sudo -u deploy bash -s' <<'EOF'
set -euo pipefail
umask 077
ENV=/srv/kairos/env/staging.env
: > /tmp/sek
# 若文件已存在，不重建（保 Master Key 稳定）
if [ -f "$ENV" ]; then echo "ENV_EXISTS_PRESERVE"; exit 0; fi
gen() { python3 -c "import secrets,sys;print(secrets.token_hex(int(sys.argv[1])//4))" "$1"; }
{
  echo "# kairos-staging env — generated $(date -u +%FT%TZ), owner deploy, DO NOT COMMIT"
  echo "POSTGRES_DB=kairos_staging"
  echo "POSTGRES_USER=kairos_staging"
  echo "POSTGRES_PASSWORD=$(gen 24)"
  echo "MINIO_ACCESS_KEY=kairos_staging"
  echo "MINIO_SECRET_KEY=$(gen 24)"
  echo "KAIROS_SESSION_SECRET=$(gen 32)"
  echo "KAIROS_CREDENTIAL_MASTER_KEY=$(gen 64)"
  echo "KAIROS_CREDENTIAL_KEY_VERSION=k1"
  echo "KAIROS_OTEL_ENABLED=true"
  echo "KAIROS_STAGING_PROJECT=kairos-staging"
} > "$ENV"
chmod 600 "$ENV"
echo "SECRETS_GENERATED"
EOF
```

约束：**绝不打印 staging.env 内容**；脚本不 echo Secret 到终端日志；Master Key（M-03）首次生成后跨 redeploy 保持稳定（幂等保留）。

- [ ] **Step 4: 记录**

execution record 追加：`/srv/kairos` 目录、secret 文件已生成（权限 600）、幂等策略确认、Docker 版本/时区/日志轮转结论。

---

## Task 4: Repository 侧部署产物（compose / reverse-proxy / scripts）+ 本地 Commit

**Files:**
- Create: `infra/compose/compose.base.yml`
- Create: `infra/compose/compose.staging.yml`
- Create: `infra/compose/compose.staging.override.yml`（nginx vhost 注入共享 nginx 的 override）
- Create: `infra/reverse-proxy/zz-kairos-staging-tls.conf`（staging vhost 模板，真实域名与上游）
- Create: `infra/scripts/deploy-staging.sh`
- Create: `infra/scripts/migrate-staging.sh`
- Create: `infra/scripts/smoke-staging.sh`
- Create: `infra/scripts/rollback-staging.sh`
- Modify: `infra/scripts/`（如已有同能力脚本则增强，不制造重复）

**Interfaces:**
- Consumes: Task 1 拓扑（共享 nginx / lumina-prod-internal / Docker DNS 上游名）、Task 3 的 env 常量。
- Produces: 可重复部署的版本化 compose、vhost、脚本；本地 2～4 个 Commit。

- [ ] **Step 1: 编写 `infra/compose/compose.base.yml`**

内容要点（从 `infra/compose/compose.yaml` 演进，避免维护两套无关 Compose）：
- 服务：`postgres`(postgres:16-alpine)、`temporal`(temporalio/auto-setup:1.26.2)、`minio`(minio/minio)、`minio-init`(minio/mc)、`otel-collector`(otel/opentelemetry-collector:0.105.0)、`migrate`、`api`、`worker`、`web`。
- 后端环境锚点 `x-backend-env` 引用 `${...}` 变量（无硬编码 Secret），DB/Temporal/MinIO 走容器名；`KAIROS_ENV=staging`、`KAIROS_SESSION_COOKIE_SECURE=true`、`KAIROS_CORS_ORIGINS=["https://staging.kairos.ac.cn"]`。
- 内部服务**不发布宿主端口**；volume 持久化。
- 端口仅 `api` 内部 8000、`web` 内部 80（供反向代理）。
- 无 `latest` tag：镜像 tag 来自脚本注入的 `KAIROS_WEB_IMAGE / KAIROS_API_IMAGE / KAIROS_WORKER_IMAGE`（`staging-<gitsha>`）。

验证：`docker compose -f infra/compose/compose.base.yml -f infra/compose/compose.staging.yml config -q` 预期 PASS。

- [ ] **Step 2: 编写 `infra/compose/compose.staging.yml`**

- 覆盖私有网络 `kairos-staging-internal`；`web`/`api` 额外 join 外部网络 `lumina-prod-internal`（`external: true`），并设置 container_name 别名 `kairos-web`/`kairos-api`（保证 Docker DNS 上游名稳定）。
- 引用 `/srv/kairos/env/staging.env`（`env_file`）；project name `kairos-staging`。
- `restart: unless-stopped`（migrate 除外，`restart: "no"`）。

验证：`docker compose -f compose.base.yml -f compose.staging.yml config -q` 预期 PASS。

- [ ] **Step 3: 编写 `infra/reverse-proxy/zz-kairos-staging-tls.conf`**

按 stellaris 同款模板，真实值写死：

```nginx
# kairos-staging TLS vhost（挂载进共享 lumina nginx 容器，同 aurora/stellaris 模式）
# 证书：宿主 certbot /etc/letsencrypt/live/staging.kairos.ac.cn/

server {
    listen 80;
    listen [::]:80;
    server_name staging.kairos.ac.cn;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
        try_files $uri =404;
    }
    location / { return 301 https://staging.kairos.ac.cn$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name staging.kairos.ac.cn;
    server_tokens off;
    ssl_certificate /etc/letsencrypt/live/staging.kairos.ac.cn/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/staging.kairos.ac.cn/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:KairosStagingTLS:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    location /api/events/ {
        resolver 127.0.0.11 valid=10s ipv6=off;
        set $kairos_api http://kairos-api:8000;
        proxy_pass $kairos_api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_buffering off; # SSE 事件流
    }
    location ^~ /api/ {
        resolver 127.0.0.11 valid=10s ipv6=off;
        set $kairos_api http://kairos-api:8000;
        proxy_pass $kairos_api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location / {
        resolver 127.0.0.11 valid=10s ipv6=off;
        set $kairos_web http://kairos-web:80;
        proxy_pass $kairos_web;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

- [ ] **Step 4: 编写 `infra/compose/compose.staging.override.yml`（共享 nginx 注入）**

仅追加 nginx 服务的一个只读 bind-mount（与 aurora/stellaris 同款），显式注明「apply 时必须同时带上 aurora/stellaris override，否则会丢 vhost」：

```yaml
services:
  nginx:
    volumes:
      - type: bind
        source: /srv/kairos/deploy/nginx/conf.d/zz-kairos-staging-tls.conf
        target: /etc/nginx/conf.d/zz-kairos-staging-tls.conf
        read_only: true
```

- [ ] **Step 5: 编写部署脚本（strict mode、清晰错误、不输出 Secret）**

`infra/scripts/deploy-staging.sh`：
- 读取 `KAIROS_WEB_IMAGE/KAIROS_API_IMAGE/KAIROS_WORKER_IMAGE`（`staging-<gitsha>`）。
- 本地 `docker buildx build --platform linux/amd64` → `docker save` → `ssh deploy@47.238.145.24 docker load`。
- 同步 compose/vhost/env 到 `/srv/kairos`；`docker compose -p kairos-staging up -d`。
- 预留 `REGISTRY_IMAGE_PREFIX` 变量，未来切 Registry 无需业务重构。

`infra/scripts/migrate-staging.sh`：进入 backend 目录对 staging DATABASE_URL 执行 `alembic upgrade head`（或通过 migrate 容器 `docker compose run --rm migrate`）。

`infra/scripts/smoke-staging.sh`：Task 6/7 的 smoke 命令封装（health、auth、ownership、credential、temporal、checkpoint、secret scan），退出码非 0 即失败。

`infra/scripts/rollback-staging.sh`：记录 `PREVIOUS_STAGING_IMAGE`（首次为 `FIRST_STAGING_RELEASE`），`docker compose down/up` 同一 digest 或切回上一 tag 并复跑 smoke。

验证：`bash -n` 每个脚本 PASS；`docker compose config -q` PASS。

- [ ] **Step 6: 本地 Commit（2～4 个，可独立验证）**

```bash
git add infra/compose/compose.base.yml infra/compose/compose.staging.yml infra/compose/compose.staging.override.yml
git commit -m "build(infra): add kairos-staging compose base and staging

从 M-01 本地 compose 演进出 base+staging 双层结构，内部服务不暴露宿主端口，
web/api join 共享 lumina-prod-internal 网络供反向代理 Docker DNS 访问，镜像 tag 由脚本注入。
关联模块：DEPLOY-GATE-1"

git add infra/reverse-proxy/zz-kairos-staging-tls.conf infra/compose/compose.staging.override.yml
git commit -m "build(infra): add staging.kairos.ac.cn vhost for shared nginx

沿用 aurora/stellaris 的共享 nginx bind-mount 模式注入 kairos vhost，路由 /api/events/ 关闭
proxy_buffering 以预留 SSE，证书由宿主 certbot 签发。关联模块：DEPLOY-GATE-1"

git add infra/scripts/deploy-staging.sh infra/scripts/migrate-staging.sh infra/scripts/smoke-staging.sh infra/scripts/rollback-staging.sh
git commit -m "chore(deploy): add staging deploy, migrate, smoke and rollback scripts

strict mode、清晰错误、不输出 Secret，预留 REGISTRY_IMAGE_PREFIX 便于未来切换镜像发布方式。
关联模块：DEPLOY-GATE-1"
```

预期：3 个 commit 成功；`docker compose config -q` 与 `bash -n` 全部 PASS。

---

## Task 5: 构建不可变镜像 → 传输 → Compose 部署 kairos-staging

**Files:**
- Modify（服务器）: `/srv/kairos/compose/*`、`/srv/kairos/env/staging.env`（已由 Task 3/4 生成）
- Create（服务器）: `/srv/kairos/deploy/nginx/conf.d/zz-kairos-staging-tls.conf`（从 repo 同步）

**Interfaces:**
- Consumes: Task 4 的 compose/vhost/脚本；Task 3 的 secret env。
- Produces: 服务器上运行的 `kairos-staging` 全套容器（web/api/worker/postgres/temporal/minio/otel），镜像 tag 含 Git SHA。

- [ ] **Step 1: 确认服务器架构并本地构建不可变镜像**

服务器架构已探明 `x86_64`（amd64）。本地构建：

```bash
SHA=$(git rev-parse --short=12 HEAD)
docker buildx build --platform linux/amd64 --load -t kairos-web:staging-$SHA frontend/
docker buildx build --platform linux/amd64 --load -t kairos-api:staging-$SHA backend/
docker buildx build --platform linux/amd64 --load -t kairos-worker:staging-$SHA backend/  # 与 api 同 Dockerfile，command 由 compose 覆盖
docker images --format "{{.Repository}}:{{.Tag}} {{.ID}} {{.Digest}}" | grep "staging-$SHA"
```

预期：三个镜像构建成功；记录 image ID / digest / Git SHA（写入 execution record）。

- [ ] **Step 2: 传输镜像（docker save → SSH → docker load）**

```bash
SHA=$(git rev-parse --short=12 HEAD)
docker save kairos-web:staging-$SHA kairos-api:staging-$SHA kairos-worker:staging-$SHA \
  | ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 'docker load'
```

预期：服务器 `docker images` 出现三个 `staging-$SHA` tag；记录 digest。传输后清理本地/服务器多余 tar（本次未落 tar，走管道，天然无残留）。

- [ ] **Step 3: 同步 compose / vhost / env 到服务器**

```bash
scp -i ~/.ssh/kairos_staging_deploy_rsa infra/compose/compose.base.yml infra/compose/compose.staging.yml \
    infra/compose/compose.staging.override.yml deploy@47.238.145.24:/srv/kairos/compose/
scp -i ~/.ssh/kairos_staging_deploy_rsa infra/reverse-proxy/zz-kairos-staging-tls.conf \
    deploy@47.238.145.24:/srv/kairos/deploy/nginx/conf.d/
```

预期：文件就位，权限正确（env 600）。

- [ ] **Step 4: 启动 kairos-staging（内部健康检查等待）**

```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  "cd /srv/kairos/compose && docker compose -p kairos-staging \
     -f compose.base.yml -f compose.staging.yml \
     up -d --wait 2>&1 | tail -20"
```

预期：postgres/temporal/minio/otel/api/worker/web 全部 healthy；`docker compose ps` 全 Up。

- [ ] **Step 5: 记录部署事实**

execution record 追加：镜像 tag/digest、Git SHA、compose project `kairos-staging`、容器状态。

---

## Task 6: Migration + 内部 Gate Smoke（不依赖公网域名）

**Files:**
- Modify（服务器）: `/srv/kairos/env/staging.env`（不变）
- Create（服务器）: `/tmp/kairos-staging-smoke`（临时 smoke 输出，结束后清理）

**Interfaces:**
- Consumes: Task 5 运行的容器栈。
- Produces: Migration 0004 确认；health/auth/ownership/credential/temporal/M-04 checkpoint 全部 PASS 证据。

- [ ] **Step 1: Migration 到 head（干净 staging DB）**

```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  "cd /srv/kairos/compose && docker compose -p kairos-staging -f compose.base.yml -f compose.staging.yml run --rm migrate"
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  "docker compose -p kairos-staging exec postgres psql -U kairos_staging -d kairos_staging -c 'select version_num from alembic_version;'"
```

预期：`alembic_version.version_num = 0004`（仓库当前 head）；迁移日志留存。禁止手工建表。

- [ ] **Step 2: 内部 Health（不依赖域名，用容器内/网络内访问）**

```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  "docker compose -p kairos-staging exec api python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/health/live').read().decode())\""
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  "docker compose -p kairos-staging exec api python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/health/ready').read().decode())\""
```

预期：`live` 返回 `status=ok`；`ready` 返回 200 且 postgresql/temporal/object_storage 全 ok。

- [ ] **Step 3: Auth Smoke（Gate Test User A/B）**

在 API 容器内用随机测试邮箱执行 register/login/session 链路（复用仓库 smoke/测试工具或直接 API）。记录 A/B 用户 id 与 cookie 语义，结果 PASS。

- [ ] **Step 4: Ownership Smoke**

User A 创建一个当前已有 owner-owned resource（如 Task，M-04 已建），User B 尝试读取/修改。预期安全拒绝/404 policy PASS。**不创建 M-05+ API**。

- [ ] **Step 5: Credential Security Smoke**

User A 保存固定假值 `GATE_TEST_SECRET`（非真实商业 API Key）。验证：API Response 无明文；DB 字段无明文；应用日志无明文。完成后清理测试 Provider/Credential。

- [ ] **Step 6: Temporal + M-04 Checkpoint Smoke**

运行 M-01 已有最小 Workflow→Activity（`smoke_workflow`，queue `kairos-smoke`，worker 已监听），并跑 M-04 domain transaction→event→outbox→checkpoint（复用 `tests/domain/test_domain_smoke.py` 逻辑在 staging 环境以服务方式验证）。预期均成功写 checkpoint。

- [ ] **Step 7: 记录结果**

execution record 追加：Migration=0004、health live/ready、auth、ownership、credential security、temporal、M-04 checkpoint 全部 PASS（真实命令与输出摘要）。

---

## Task 7: DNS + HTTPS + 共享 nginx vhost 激活（预计 BLOCKED_DNS_AUTH）

**Files:**
- Modify（服务器）: `/etc/letsencrypt`（certbot 证书，DNS 生效后）
- Modify（服务器）: 共享 nginx 挂载（`/srv/kairos/deploy/nginx/conf.d/zz-kairos-staging-tls.conf` 注入）

**Interfaces:**
- Consumes: Task 1 的 DNS 现状（无 A 记录）；Task 4 的 vhost 模板；Task 6 确认 api 可服务。
- Produces: `https://staging.kairos.ac.cn` 真实 HTTPS（DNS 授权存在时）；否则精确记录唯一 Block。

- [ ] **Step 1: 再次确认 DNS 解析状态**

```bash
nslookup staging.kairos.ac.cn 223.5.5.5
```

预期：若已返回 `47.238.145.24` → 继续 Step 2；若仍 NXDOMAIN → 无 Alibaba control-plane 授权（本机/服务器均无 aliyun CLI、无凭据、无 browser automation 工具），记为 `BLOCKED_DNS_AUTH`，跳到 Step 5。

- [ ] **Step 2: certbot 签发 staging 证书（仅 DNS 已生效时）**

```bash
ssh -o BatchMode=yes stellaris-server \
  "sudo certbot certonly --webroot -w /var/www/certbot -d staging.kairos.ac.cn --agree-tos -n --keep-until-expiring"
```

预期：证书签发成功于 `/etc/letsencrypt/live/staging.kairos.ac.cn/`；自动续期（certbot renew 定时器）确认。

- [ ] **Step 3: 将 kairos vhost 注入共享 nginx（非破坏性，保留既有 vhost）**

先记录当前挂载；以 compose 方式同时携带 aurora/stellaris/kairos 三个 override 应用（避免丢 vhost）：

```bash
ssh -o BatchMode=yes stellaris-server \
  "docker compose -f /opt/lumina/app/deploy/compose.prod.yml \
     -f /opt/aurora-preview/deploy/compose.aurora-override.yml \
     -f /opt/stellaris/deploy/compose.stellaris-override.yml \
     -f /srv/kairos/compose/compose.staging.override.yml up -d nginx"
docker exec lumina-prod-nginx-1 nginx -t
docker exec lumina-prod-nginx-1 nginx -s reload
```

约束：应用前备份 lumina compose；`nginx -t` PASS 后才 reload；验证 lumina.ac.cn / stellaris.ac.cn / aurora.ah.cn 仍 200，且 `https://staging.kairos.ac.cn` 返回 Kairos web。

- [ ] **Step 4: 公网 HTTPS Smoke（DNS 生效后）**

```bash
curl -fsS https://staging.kairos.ac.cn/ | head -5
curl -fsS https://staging.kairos.ac.cn/api/health/live
curl -fsS https://staging.kairos.ac.cn/api/health/ready
```

预期：全部 200；证书有效（不忽略证书错误，不用 self-signed 冒充 PASS）。

- [ ] **Step 5: 记录 Block（若无授权）**

若 Step 1 仍无解析且无任何 Alibaba DNS 授权方式：execution record 记 `BLOCKED_DNS_AUTH`，唯一所需动作写入最终报告：
`在 kairos.ac.cn DNS Zone 创建记录：staging A 47.238.145.24`。
其余服务器工作（Task 2～6、8）不受影响，照常完成并用内部端点验证。

---

## Task 8: Restart / Persistence / Rollback Readiness + Execution Record + 最终报告

**Files:**
- Modify: `docs/implementation/DEPLOY-GATE-1-execution.md`
- Create（服务器）: `/srv/kairos/releases/manifest-<gitsha>.json`

**Interfaces:**
- Consumes: Task 5/6 运行结果。
- Produces: 重启持久化验证、`FIRST_STAGING_RELEASE` 回滚就绪、release manifest、最终 Gate 结论。

- [ ] **Step 1: Persistence 验证**

记录测试 DB row + MinIO test object（Task 6 Smoke 产物），然后重启 api/worker：

```bash
ssh -i ~/.ssh/kairos_staging_deploy_rsa deploy@47.238.145.24 \
  "docker compose -p kairos-staging -f /srv/kairos/compose/compose.base.yml -f /srv/kairos/compose/compose.staging.yml restart api worker"
```

预期：DB 数据仍存在、MinIO object 仍存在、api ready、Temporal 重新连接正常。

- [ ] **Step 2: Restart 恢复（容器级，不无理由 reboot 整机）**

确认 `restart: unless-stopped`；如安装了需 reboot 的系统组件，先说明原因并确认 SSH key / Docker auto-start / volumes 可恢复后再执行。本轮预计只需容器 recreate 验证。

- [ ] **Step 3: Rollback Readiness**

首次 Staging 发布，无 Previous Image → 记录 `FIRST_STAGING_RELEASE`。验证当前不可变镜像可通过 `docker compose down/up` 或 re-deploy same digest 恢复。保存 release manifest（Git SHA、image tags、image IDs/digests、migration、deploy time、domain；**不含 Secrets**）。

- [ ] **Step 4: Secret Leak 扫描**

搜索 `GATE_TEST_SECRET` 在 application logs / API response / DB visible fields 不出现；不打印整个 staging.env。

- [ ] **Step 5: 更新 Execution Record 与最终状态**

`docs/implementation/DEPLOY-GATE-1-execution.md` 完整记录：Status、Baseline SHA、Deploy branch、Server（IP/OS/arch/deploy user/public-key fingerprint/Docker）、DNS Result、HTTPS Result、Images（tag/digest）、Migration、Smoke（health/auth/ownership/credential/temporal/checkpoint/restart/secret leak）、Rollback Readiness、Final Status。**不记录 password/private key/Master Key/DB 密码/MinIO secret。**

- [ ] **Step 6: 最终报告 + 结束**

输出 `DEPLOY-GATE-1: PASS / BLOCKED`（按真实结果）。若 DNS 阻塞则 `BLOCKED_DNS_AUTH` 且仅列 `staging A 47.238.145.24` 这一条用户动作；其余全部 PASS 如实报告。**不开始 M-05。**

---

## Self-Review（writing-plans）

### 1. Spec Coverage
- 用户需求 6～8 个 Task → 本计划 8 个 Task 对齐。
- SSH key 自动生成 + deploy 用户 + 加固 → Task 2。
- 服务器非空审计 + 共享 nginx 复用 vhost → Task 1 审计 + Task 7 注入。
- DNS 自动 → 尽力尝试，无授权则 `BLOCKED_DNS_AUTH` 精确报告 → Task 7 Step 5。
- 镜像不可变 + Git SHA + bootstrap transport → Task 5。
- Migration 0004 + 干净 DB → Task 6 Step 1。
- Smoke（health/auth/ownership/credential/temporal/checkpoint/persistence/secret scan）→ Task 6 + Task 8。
- Rollback Readiness + release manifest → Task 8。
- Git 分支 `ci/deploy-gate-1-staging` + 2～5 Commit + 不 push → Task 1/4。
- Execution record → Task 1/8。
- 不跑无关 full suite、不开始 M-05、不部署 Production → Global Constraints + Task 8。

### 2. Placeholder Scan
- 无 `example.com` / `<server-ip>` / `<TBD>` / `TODO`。所有 IP `47.238.145.24`、域名 `staging.kairos.ac.cn` 为真实值。
- 唯一动态值为 `$(git rev-parse --short=12 HEAD)`（部署时真实生成），非占位符。

### 3. Type Consistency
- 上游名 `kairos-web:80` / `kairos-api:8000` 在 compose.staging.yml 与 vhost conf 中一致。
- image tag `staging-<gitsha>` 在 compose.base.yml 注入变量、Task 5 build、manifest 中一致。
- env 变量名（`POSTGRES_DB`/`KAIROS_SESSION_SECRET`/`KAIROS_CREDENTIAL_MASTER_KEY` 等）与 backend `app/config.py` 的 `KAIROS_` 前缀、`.env.example` 对齐。
- 目录 `/srv/kairos/{compose,env,data/staging,backups/staging,scripts,releases,deploy/nginx/conf.d}` 在 Task 3 与脚本中一致。

---

## DEPLOY-GATE-1 SELF APPROVAL

逐项核对（全部满足 → `PLAN SELF-APPROVAL: PASS`）：

- [x] IP `47.238.145.24` 真实使用
- [x] staging 入口唯一为 `staging.kairos.ac.cn`
- [x] Production `app.kairos.ac.cn` 本轮不部署
- [x] SSH private key 在 `~/.ssh/`（repo 外），不进 Git
- [x] Secret 只存服务器 `/srv/kairos/env/staging.env`，不进 Git
- [x] DB/Temporal/MinIO 不暴露公网端口
- [x] 服务器不现场构建源码（本地 buildx → save → load）
- [x] Migration 走 Alembic 到 0004
- [x] HTTPS 真实（certbot + 共享 nginx），不用 self-signed 冒充
- [x] Smoke 真实（Task 6/8 真实命令）
- [x] 不跑无关 full test suite（A-Lite）
- [x] 无 M-05+ 范围
- [x] Git 无未经授权的 remote merge / tag / release / push

**PLAN SELF-APPROVAL: PASS** → 自动执行。
