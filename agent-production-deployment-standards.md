# 网页信息采集 Agent：线上部署规范

> 版本：v1.1  
> 日期：2026-08-10  
> 第一版部署策略：**中国香港云服务器 + 单服务器 Docker Compose + `app.example.com` 单域名同源接入；按生产标准实现 DNS、HTTPS、服务器加固、可重复部署、环境隔离、备份、监控和回滚；暂不引入 Kubernetes。**

---

## 1. 目标

第一版优先实现：

```text
快速上线
+ 可重复部署
+ 可回滚
+ 可备份恢复
+ 安全网络边界
+ Staging 持续验证
```

不追求：

```text
Kubernetes
Service Mesh
多地域
复杂自动扩缩容
多节点高可用集群
```

代码必须保持未来可拆分能力，但第一版运维保持简单。

---

## 2. 第一版运行拓扑

推荐一台服务器内使用 Docker Compose 运行独立容器角色：

```text
Internet
   ↓
Reverse Proxy / HTTPS
   ↓
Web + FastAPI API
         ↓
   PostgreSQL
   Temporal
   MinIO/S3
         ↓
Temporal Worker Pools
├─ orchestration/core worker
├─ HTTP/Scrapy worker
├─ Browser/Playwright worker
└─ LLM/Search provider worker

OTel Collector / logs / metrics
```


同一个 Worker 镜像可以通过启动参数/环境变量选择不同 Task Queue，不维护多套分叉镜像。

---

## 2A. 云服务器与域名基线

第一版 Production 固定采用**中国香港地域云服务器**。云厂商可以替换，但应用不得绑定某个厂商私有能力才能运行。

### 2A.1 云服务器原则

- 选择香港地域的主流云服务器实例。
- 操作系统使用仍处于安全支持期的 Linux LTS 发行版。
- 服务器必须具有固定公网 IPv4；如果启用 IPv6，则 DNS、防火墙和 SSH 规则必须同步配置。
- 应用配置、数据库连接、对象存储路径、Temporal 地址不得把公网 IP 写死在业务代码中。
- 所有环境差异通过环境变量、Compose 配置和 DNS 表达。
- 第一版允许应用、数据库、Temporal、MinIO 和 Worker 共用一台服务器，但必须使用独立容器、私有网络和持久化 Volume。
- 未来迁移到中国大陆或其他地域时，必须能通过“新服务器准备 → 数据同步/恢复 → Smoke Test → DNS 切换”的方式迁移，而不是修改大量业务代码。

### 2A.2 域名结构

Production 对用户只暴露一个产品域名：

```text
https://app.example.com
```

浏览器访问保持同源：

```text
https://app.example.com/             → Vue 3 Web
https://app.example.com/api/*        → FastAPI
https://app.example.com/api/events/* → FastAPI SSE
```

第一版不单独暴露 `api.example.com`，避免增加跨域 Cookie、CORS、CSRF 和 SSE 配置复杂度。

Staging 使用独立子域名，例如：

```text
https://staging.example.com
```

不得让 Staging 与 Production 共用 Session Cookie、数据库或对象存储命名空间。

### 2A.3 DNS

域名 DNS 必须明确指向当前生产服务器公网 IP。

推荐记录：

```text
app.example.com      A      <production-public-ip>
staging.example.com  A      <staging-public-ip-or-same-server>
```

如果使用 IPv6，再增加对应 AAAA 记录。

规则：

- 不在前端源码或后端业务代码中写死服务器公网 IP。
- 切换服务器前将 DNS TTL 临时调低，完成稳定观察后再恢复常规 TTL。
- 域名切换必须与 Production Release/迁移 Runbook 联动。
- 旧服务器在 DNS 切换后保留一个明确的回滚窗口，不立即销毁。
- DNS 服务商账号应开启多因素认证，并作为关键基础设施账号管理。

---

## 2B. 云服务器首次初始化

新服务器不能安装 Docker 后直接上线。首次初始化必须完成以下基线。

### 2B.1 系统账户

首次使用云厂商提供的初始管理账号完成 Bootstrap 后：

1. 创建独立运维账号，例如 `deploy`。
2. 为 `deploy` 配置 SSH Public Key。
3. 只授予部署所需的 sudo 权限。
4. 禁止应用容器使用宿主机 root 身份执行普通业务任务。
5. 禁止团队/Agent 共用私钥文件。

推荐服务器目录：

```text
/srv/kairos/
├─ compose/
├─ env/
├─ data/
│  ├─ postgres/
│  ├─ temporal/
│  └─ minio/
├─ backups/
├─ scripts/
└─ releases/
```

Secrets 目录必须设置最小文件权限，并与普通发布产物分开。

### 2B.2 系统基础设置

至少完成：

- 系统安全更新。
- NTP/时间同步。
- 时区明确配置；业务时间统一使用 UTC 存储，前端按用户时区展示。
- Docker Engine 与 Compose Plugin 安装。
- Docker 日志轮转。
- 宿主机磁盘使用监控。
- 防止 Docker/浏览器临时文件无限增长。
- 设置合理的文件描述符和进程资源上限。
- 重启后 Docker 服务和必要容器能够按预期恢复。

### 2B.3 云安全组与宿主机防火墙

云安全组和宿主机防火墙形成双层边界。

公网允许：

```text
80/tcp   → HTTP，仅用于 ACME/跳转 HTTPS
443/tcp  → HTTPS
22/tcp   → SSH，允许公网连接，但只接受密钥认证
```

禁止公网开放：

```text
5432      PostgreSQL
7233      Temporal
9000/9001 MinIO
OTel/metrics internal ports
Docker daemon socket/API
Worker internal ports
```

如果某个运维工具需要临时访问内部端口，优先使用 SSH Tunnel，不长期新增公网规则。

---


## 3. Staging 与 Production

即使部署在同一台物理服务器，也必须逻辑隔离。

必须分离：

```text
域名/入口
Compose project
环境变量
PostgreSQL database
MinIO bucket/prefix
Temporal namespace（或等价边界）
Secrets
日志标签
```

禁止 Staging 使用 Production 业务数据做测试。

示例：

```text
staging.example.com
app.example.com
```

---

## 4. Docker Compose 规范

推荐目录：

```text
infra/
├─ compose/
│  ├─ compose.base.yml
│  ├─ compose.staging.yml
│  └─ compose.production.yml
├─ reverse-proxy/
├─ otel/
└─ scripts/
```

### 4.1 镜像

Production 只部署 CI 构建并推送到受控 Registry 的不可变镜像。

例如：

```text
registry.example.com/kairos-web:0.4.0
registry.example.com/kairos-api:0.4.0
registry.example.com/kairos-worker:0.4.0
```

部署记录同时保存 image digest。

禁止 Production 服务器现场：

```text
git pull
npm build
pip install
docker build .
```

来生成“只有该服务器存在”的版本。

---

## 4A. Container Registry 与增量镜像发布

> Kairos 当前**默认 Registry 为 GitHub Container Registry（GHCR）**，镜像保持私有。

### 4A.1 标准发布路径

```text
Developer
↓
fix / feature branch
↓
PR / CI（GitHub Actions）
↓
main / Release Tag
↓
Docker BuildKit（Actions runner）
↓
immutable images → GHCR push（GITHUB_TOKEN）
↓
Staging docker pull
↓
Staging Smoke
↓
Release Tag
↓
Production docker pull
↓
Migration（如适用）
↓
Compose
↓
Health / Readiness
↓
Production Smoke
```

镜像身份（不可变）：

```text
ghcr.io/gehrmannmerlin/kairos-web:   <sha12>           # main push
ghcr.io/gehrmannmerlin/kairos-api:   vX.Y.Z-<sha12>    # release tag
ghcr.io/gehrmannmerlin/kairos-worker:<immutable-tag>
```

禁止使用 `latest` / `main` / `dev` / `test` 作为唯一可追溯镜像标识。每次 Release 必须能对应 Git tag、commit SHA、web/api/worker image digest、migration version、deploy time。

### 4A.2 Layer 增量原则

OCI/Docker Registry 按 layer **content digest** 去重：

```text
已有 layer → reuse（不上传 / 不下载）
新增 layer → transfer（上传 / 下载）
```

因此“增量部署”指 **增量传输 Docker layers**，而不是上传 Git/source diff 覆盖服务器文件。服务器最终仍运行完整 immutable image。

### 4A.3 禁止源码增量覆盖 Production

禁止以下作为正式发布方式：

```text
rsync changed source
scp changed source
git diff patch
container source overwrite
```

理由：Production Release 必须继续对应一个完整、不可变、可追溯的镜像身份。

### 4A.4 GitHub Actions 构建与推送

- 标准构建在 `.github/workflows/ci-build-push.yml`（`main` push → `<sha12>` tag；`v*` tag push → `vX.Y.Z-<sha12>`）。
- 使用仓库 `GITHUB_TOKEN`（`packages: write`）完成 `docker login ghcr.io` 与 push，**不把长期 Registry 密码写入仓库**。
- 启用 BuildKit layer cache（`type=gha`）：dependency 未变化时只重新构建 application layer。
- 仓库需开启 package write 权限（Settings → Actions → General → Workflow permissions 允许 GITHUB_TOKEN 写 packages），镜像默认私有。

### 4A.4.1 GHCR 私有包 bootstrap 模式

**背景**：`GITHUB_TOKEN` 从**公开仓库**发布到 GHCR 时，新建的包会按仓库可见性被创建为 **public**，且一旦 public 无法改回 private（GitHub 硬规则，已实测复现两次）。因此私有镜像必须走 bootstrap：

```text
首次：PAT classic（write:packages）手动 push 创建 PRIVATE 包 + 连接仓库
      ↓
长期：GitHub Actions GITHUB_TOKEN 基于仓库 linkage 持续写入同一 PRIVATE 包
      （不重建包、不重置可见性）
```

步骤：

1. **镜像 source label**：web/api/worker 的 `Dockerfile` 在末尾（EXPOSE 前）加
   `LABEL org.opencontainers.image.source=https://github.com/GehrmannMerlin/Kairos`
   （放在末尾避免使依赖层失效；仅元数据，不改变 runtime）。
2. **一次性 bootstrap**：本机用 **PAT classic（仅 `write:packages` scope，短过期）**
   `docker login ghcr.io` → 构建带 label 的 3 镜像 → `docker push ghcr.io/gehrmannmerlin/kairos-{web,api,worker}:<tag>`，
   创建 **PRIVATE** 包。PAT 只经交互式 `docker login`，不进入 Git / 对话 / 日志 / Actions Secret。
3. **连接仓库**：包页面 → `Connect repository` → 选择 `GehrmannMerlin/Kairos`；
   然后 Package settings → **「Inherit access from repository (recommended)」** 开启
   （或在 Manage Actions access → Add repository → 选仓库 → Role `Write`）。
   否则 workflow 的 `GITHUB_TOKEN` 只有 `read_package`，push 会 403 `permission_denied: read_package`。
4. **验证 GITHUB_TOKEN 续推**：触发 `ci-build-push.yml`（GITHUB_TOKEN），新 immutable tag push
   必须成功，且包 **保持 PRIVATE**（匿名 `docker pull` 必须失败）。
5. **撤销 bootstrap PAT**：续推验证通过后**立即撤销/删除** bootstrap 用 `write:packages` PAT。
   bootstrap PAT 不得变成长期 Actions Secret，也不得作为部署凭据长期存在。
6. **服务器 pull**：服务器使用**独立、最小权限** PAT classic（仅 `read:packages`，
   不含 `write:packages`/`delete:packages`），交互式 `docker login ghcr.io`，凭据存
   `/home/deploy/.docker/config.json`（0600），不进入 Git / 对话 / 日志。


### 4A.5 Dockerfile Layer 规则

目标顺序：

```text
base/runtime
↓
dependency manifest（pyproject / requirements / package.json + lock）
↓
install dependencies
↓
application source
```

- backend：`COPY pyproject.toml` 后先 `pip install`（stub `app/__init__.py` 满足 setuptools 包发现），再 `COPY app` + 快速重装本地包。业务代码修改不会让 dependency layer 全部失效。
- frontend：`COPY package.json + lock` → `npm ci` → `COPY .` → `npm run build`。
- 两个 `.dockerignore` 排除 `.git`、`.env*`、`node_modules`、`dist`、`tests` 等，避免无关文件进层。

### 4A.6 服务器端标准行为

服务器（47.238.145.24）正常发布时只允许：

```text
docker login（凭据在 ~/.docker/config.json，0600）
docker pull <immutable image>
migration
docker compose up
health / readiness
smoke test
rollback
```

禁止：

```text
git pull
docker build .
pip install
npm install / npm build
rsync / scp application source
vim source
docker exec 修改源码
```

服务器是 **deployment target**，不是 build machine，也不是 development workspace。

### 4A.7 服务器 pull 凭据

- Staging / Production 服务器使用**最小权限 `read:packages`** 凭据（fine-grained PAT，或 GitHub App `packages: read`），只允许 pull private images。
- `docker login ghcr.io` 在服务器交互执行，密码/PAT **不得进入 Claude 对话、Git 或部署脚本**；Docker 将其存入 `/home/deploy/.docker/config.json`（0600）。

### 4A.8 Staging 发布

```text
registry 上已存在 immutable tag（CI push 或 registry-push.sh）
↓
REGISTRY=ghcr.io NAMESPACE=gehrmannmerlin RELEASE_TAG=<tag> ./infra/scripts/deploy-staging.sh
  = 服务器 docker pull → 同步 compose/vhost/otel → compose config -q → compose up
↓
smoke-staging.sh（health / auth / ownership / credential / checkpoint / secret scan）
↓
Provider smoke（如适用）
```

### 4A.9 Production 发布

```text
Staging RC PASS
↓
创建 Release Tag（vX.Y.Z）
↓
确认 immutable images/digests（CI push vX.Y.Z-<sha12>）
↓
Pre-release Backup（ENV=production ./infra/scripts/backup.sh）
↓
BACKUP_ID / PREVIOUS_RELEASE / ROLLBACK_TARGET 记录
↓
RELEASE_TAG=vX.Y.Z-<sha12> ./infra/scripts/deploy-production.sh
  = 服务器 docker pull → 写 release manifest → infra up（无 --wait，init 容器兼容）
  → migrate/api/worker/web up（--wait）→ health / readiness
↓
Production Smoke（含 secret log scan）
↓
Rollback readiness 记录
```

### 4A.10 回滚

部署前记录当前 digest；失败时：

```text
compose 指向上组 image digest
↓
docker pull（如本地无该层）
↓
compose up
↓
health / smoke
```

若 DB migration 不可逆：遵守 expand/contract 规范，**不把镜像回滚与数据库回滚混为一谈**。

### 4A.11 docker save / SSH 传输（EMERGENCY ONLY）

`docker save | ssh docker load` **不再是标准发布方式**。仅允许 Break-glass / Registry outage / Disaster Recovery 场景：

- 明确记录原因与时间；
- 保留 image digest；
- 验证传输完整性；
- 完成 Smoke；
- 事后恢复 Registry 标准路径。

Break-glass 脚本：`infra/scripts/deploy-staging-breakglass.sh`、`deploy-production-breakglass.sh`。

### 4A.12 Deployment Fail Fast

任何 `build / push / pull / migration / compose / health / smoke` 失败，部署脚本必须返回 **non-zero**。禁止“后台任务显示 exit 0 但内部部署已经失败”（例如用 `| tail` 掩盖脚本退出码）。

### 4A.13 部署前后检查

部署前：重新读取本规范 → 确认 release identity / image digests / rollback target / backup 状态。
部署后：Liveness → Readiness → 登录 → 小任务 → Workflow/Worker → 数据写入 → Evidence/CSV → 日志无 Secrets → 发布记录归档。

---

## 5. 网络边界

公网只开放必要端口：

```text
80   → HTTP，仅用于证书签发/续期与跳转 HTTPS
443  → HTTPS，产品正式入口
22   → SSH，对公网开放，但必须执行本节 SSH 强制加固
```

以下服务不得直接暴露公网：

```text
PostgreSQL
Temporal
MinIO Admin/API
OTel Collector
内部 Worker
Docker daemon
```

它们只能通过 Docker 私有网络/服务器内部网络访问。

### 5.1 SSH 公网开放的强制加固

本项目第一版明确选择：**22 端口可对公网开放，但服务器只允许 SSH Key 登录。**

因此以下规则全部为强制项：

```text
PasswordAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
```

同时要求：

- 使用独立 `deploy` 用户，不使用 root 作为日常运维账号。
- `deploy` 用户只安装必要的公钥。
- 私钥不得进入 Git、CI 日志、项目目录或共享网盘。
- SSH 私钥必须有本地文件权限保护；建议使用带口令的私钥。
- 降低 `MaxAuthTries`，限制异常认证尝试。
- 启用 Fail2ban 或等价 SSH 暴力尝试封禁机制。
- 云安全组和宿主机防火墙都不得开放除 22/80/443 以外的业务无关公网端口。
- 定期检查 `/var/log/auth.log` 或等价认证日志。
- 删除离职/废弃设备的公钥后必须立即生效。
- 一旦私钥疑似泄露，必须轮换对应 Key，不得仅依赖“攻击者可能不知道服务器 IP”。

说明：公网开放 SSH 的攻击面高于 IP 白名单/VPN 方案，因此密钥认证、禁 root、失败封禁和最小 sudo 权限是不可取消的补偿控制。未来可以升级到固定 IP 白名单、VPN 或 Zero Trust，但不是第一版上线前置条件。

### 5.2 SSH 不承担发布事实来源

SSH 只用于服务器初始化、故障诊断和执行受控部署脚本。

禁止通过 SSH：

```text
vim 直接改线上源码
进入容器改 Python 文件
手工替换前端 dist
临时 pip/npm 安装后不回写 Git
```

线上真正运行的版本必须能够从 Git Tag、镜像 Tag/Digest 和部署记录还原。

---

## 6. HTTPS、域名与反向代理

Production 唯一公开应用入口：

```text
https://app.example.com
```

建议第一版优先使用 **Caddy** 作为反向代理，以减少证书签发/续期配置；若团队已经标准化 Nginx，可使用等价 Nginx 配置，但必须满足同样的安全和 SSE 要求。

### 6.1 路由

```text
/              → Vue 3 Web
/api/*         → FastAPI
/api/events/*  → FastAPI SSE
```

浏览器不得直接连接 API 容器的内部端口。

### 6.2 HTTPS

必须：

- DNS 已正确解析到服务器公网 IP 后再签发证书。
- Production 强制 HTTPS。
- HTTP 自动跳转 HTTPS。
- 自动证书续期。
- 证书续期失败必须产生运维告警。
- Secure Cookie。
- HttpOnly Cookie。
- SameSite 策略。
- 登录限流。
- 正确处理 `X-Forwarded-For` / `X-Forwarded-Proto` 等可信代理头。

由于第一版采用单域名同源结构，原则上不启用宽泛跨域 CORS。只有明确出现外部 API 客户端需求时才增加受控 Origin。

### 6.3 SSE

反向代理必须针对 SSE：

- 禁止对事件流做不适当响应缓冲。
- 保持足够长的读取超时。
- 支持连接断开后前端重连。
- 不把代理层连接存活当成任务状态事实来源。

### 6.4 Web 安全响应头

至少配置合理的：

```text
Strict-Transport-Security
X-Content-Type-Options
Referrer-Policy
Content-Security-Policy（根据前端实际资源逐步收紧）
```

不要复制一份过度严格但会导致前端无法工作的模板；以 Staging 验证通过的配置进入 Production。

---

## 7. Secrets 与配置

生产 Secrets 绝对禁止进入 Git。

包括：

```text
DB password
session secret
encryption master key
provider API keys
MinIO/S3 secret
Temporal credentials
SMTP/other future secrets
```

第一版可以使用服务器受控 `.env`/secret file，但必须：

- 权限最小化。
- 不进镜像。
- 不写日志。
- 不在 CI 输出。
- 有明确备份/轮换流程。

`.env.example` 只保留变量名。

---

## 8. 数据持久化

PostgreSQL、Temporal 数据、MinIO/S3 文件必须使用持久化 Volume 或外部存储。

禁止将业务数据只存容器可写层。

容器重建后：

```text
任务
用户
Record
Evidence
Snapshot
CSV
配置
状态事件
```

不得丢失。

---

## 9. 数据库 Migration 发布策略

部署顺序必须考虑 API/Worker 与 Migration 的兼容性。

推荐：

```text
备份
↓
运行兼容 Migration
↓
启动新 API/Worker
↓
Readiness
↓
Smoke Test
```

破坏性 Schema 变化使用 expand/contract。

禁止：

- 服务器手工修改表结构。
- Migration 失败后直接“改数据库改到能跑”。
- 新代码依赖尚未执行的 Migration。

Migration version 必须进入发布记录。

---

## 10. Staging 持续部署

采用：

```text
短生命周期分支
↓
PR/CI
↓
合并 main
↓
构建不可变镜像
↓
自动/半自动部署 Staging
↓
Smoke Test
```

`main` 的目标是始终保持可部署 Staging。

实施计划中的强制部署 Gate 继续有效：

```text
M-01～M-04  → DEPLOY-GATE-1
M-05～M-08  → DEPLOY-GATE-2
M-09～M-12  → DEPLOY-GATE-3
M-13～M-15  → DEPLOY-GATE-4
M-16～M-18  → DEPLOY-GATE-5 / Production
```

Gate 失败时先修服务器/配置/兼容问题，不继续向后堆本地模块。

---


## 10A. 云服务器部署目录与发布方式

服务器上的 `/srv/kairos` 只保存部署配置、环境文件、持久化数据、脚本和发布元数据，不作为开发工作区。

推荐：

```text
/srv/kairos/
├─ compose/
│  ├─ compose.base.yml
│  ├─ compose.staging.yml
│  └─ compose.production.yml
├─ env/
│  ├─ staging.env
│  └─ production.env
├─ data/
├─ backups/
├─ scripts/
│  ├─ deploy.sh
│  ├─ smoke-test.sh
│  ├─ backup.sh
│  └─ restore-verify.sh
└─ releases/
```

`production.env` 等敏感文件：

- 不进入 Git。
- 不打进 Docker 镜像。
- 只允许受控运维用户读取。
- 变更必须记录时间、操作者和影响范围。

### 10A.1 发布产物来源

服务器只做：

```text
docker login
docker pull <immutable image>
docker compose up
migration
health/smoke test
```

服务器不做应用源码构建。

### 10A.2 宿主机磁盘

至少分别监控：

- 根分区。
- Docker data-root。
- PostgreSQL 数据目录。
- MinIO 数据目录。
- Browser 临时文件目录。
- 备份目录。

达到预警阈值时，优先停止产生新的重型临时数据，避免 PostgreSQL/MinIO 因磁盘耗尽发生不可恢复故障。

---

## 10B. 首次域名上线 Runbook

第一次把 Production 从“服务器 IP 可访问”升级为正式域名时，按以下顺序执行：

```text
1. 创建香港云服务器
2. 完成系统初始化和 SSH Key 加固
3. 配置云安全组/宿主机防火墙
4. 安装 Docker / Compose
5. 创建 /srv/kairos 目录与持久化 Volume
6. 准备 Production Secrets
7. 部署 PostgreSQL / Temporal / MinIO
8. 部署 API / Worker / Web
9. 使用服务器内部地址完成 Health Check
10. 创建 app.example.com DNS A 记录
11. 等待 DNS 生效并验证解析
12. 启动 Caddy/Nginx 并签发 TLS
13. 验证 HTTPS / Cookie / SSE
14. 运行 Production Smoke Test
15. 建立服务器外备份
16. 执行一次 Restore Drill
17. 才允许标记“正式上线”
```

仅仅 `docker compose up -d` 成功，不代表 Production 上线完成。

---

## 10C. 香港服务器未来迁移 Runbook

服务器迁移不允许“停旧机 → 再慢慢搭新机”。

标准流程：

```text
新服务器准备
↓
安装相同部署基础环境
↓
恢复/同步 PostgreSQL 与对象存储
↓
部署与旧环境兼容的相同 Release
↓
内部 Smoke Test
↓
降低 DNS TTL
↓
短暂停止会产生冲突的新写入（如迁移方案需要）
↓
做最终增量同步/一致性检查
↓
切换 app.example.com DNS
↓
验证 HTTPS / 登录 / Task / Worker / Evidence / CSV
↓
保留旧服务器回滚窗口
↓
确认稳定后再销毁旧服务器
```

域名是稳定入口，公网 IP 不是业务身份。

---

## 11. Production 发布

Production **不随 main 自动发布**。

流程固定为：

```text
Staging RC 通过
↓
创建 Version Tag
↓
CI 重新验证
↓
构建/确认不可变镜像
↓
人工发布门禁
↓
发布前备份
↓
Production pull
↓
Migration
↓
Compose rolling/recreate
↓
Health / Readiness
↓
Production Smoke Test
↓
记录发布结果
```

---

## 12. Health 与 Readiness

至少区分：

### Liveness

回答进程是否活着。

### Readiness

回答当前实例是否适合接收流量。

API Readiness 应检查必要依赖的可用性，但避免因为一个非核心第三方 Provider 临时故障把整个 API 判为不可用。

Worker 必须能够暴露或记录：

```text
Task Queue
worker identity
heartbeat/last activity
current concurrency
```

---

## 13. Worker 并发与资源池

采用三级调度：

```text
全局任务限制
+ 单用户活跃任务限制
+ 节点级资源池
```

不同 Worker Pool 独立限制，例如：

```text
HTTP/Scrapy     高并发
Browser         低并发
LLM/Search      独立并发
```

具体数字属于部署参数，不写死到 CollectionSpec。

资源不足时业务状态显示 `WAITING_RESOURCE`，不得误报失败。

---

## 14. Browser Worker 生产限制

Playwright/Browser Worker 最容易消耗 CPU/内存。

必须：

- 设置最大并发。
- 设置页面/Context 生命周期。
- 设置超时。
- 清理临时 Profile。
- 限制下载目录。
- 防止浏览器残留进程。
- 对域名熔断/失败进行监控。
- 不让普通用户直接指定“开多少浏览器”。

必要时它是未来第一个拆到独立服务器的 Worker。

---

## 15. 日志与可观测性

所有服务使用统一 Trace/Correlation ID。

重点关联：

```text
API request
Task
Run
Node
Temporal Workflow/Activity
Model call
Search call
Fetch/Browser call
Artifact/Evidence
```

不得记录 Secrets。

生产至少监控：

```text
API 5xx
登录失败率
Task failure
Workflow failure
Activity retry
Browser crash
Provider auth/rate-limit
DB connection
DB disk
MinIO disk
CPU
memory
disk
container restart
```

第一版不要求复杂 APM 平台，但必须能够定位“哪一个 Task/Run/Node 出错”。

---

## 16. 备份规范

Production 必须至少备份：

```text
PostgreSQL
MinIO/S3 业务对象
关键 Secret/主密钥的安全副本
部署配置
```

备份必须存到**服务器之外**，否则单机磁盘损坏时备份同时丢失。

### 16.1 发布前备份

每次 Production Release 前必须生成可追溯备份点。

### 16.2 定期备份

建立自动计划，例如：

```text
PostgreSQL 每日
对象存储增量/生命周期备份
配置/密钥按变更备份
```

具体保留周期可按服务器资源调整。

---

## 17. Restore Drill

“有备份文件”不等于“能恢复”。

在 Production 首次上线前，Staging 必须至少做一次：

```text
备份
↓
创建干净恢复环境
↓
恢复 PostgreSQL
↓
恢复对象存储
↓
启动 API/Worker
↓
验证 Task/Record/Evidence/CSV
```

没有通过 Restore Drill，不视为具备生产备份能力。

---

## 18. 原始证据生命周期

结构化业务事实长期保留。

重型对象：

```text
完整 HTML
页面正文文件
截图
浏览器快照
诊断包
临时下载
```

允许按生命周期清理，但删除前必须做引用检查：

```text
是否仍被有效 FieldEvidence/Artifact 引用？
是 → 保留
否 → 达到生命周期后可清理
```

禁止生命周期任务破坏现有证据链。

---

## 19. Smoke Test

### 19.1 每次 Staging 部署

至少验证：

- Web 可打开。
- API health/readiness。
- 注册/登录。
- PostgreSQL 读写。
- Temporal Workflow 可启动。
- Worker 能消费任务。
- MinIO/S3 上传下载。
- SSE 可连接。
- Provider 配置读取正常。

### 19.2 中后期 Staging

增加真实业务 Smoke：

```text
创建 Task
→ CollectionSpec
→ Plan
→ Temporal Run
→ Source Search/指定 URL
→ Fetch
→ Extract
→ Validate
→ Data
→ Evidence
→ CSV
```

### 19.3 Production

只运行**小规模、安全的真实任务**，不在发布 Smoke 中做大规模抓取。

---

## 20. 回滚规范

回滚前提：

- 上一个稳定镜像仍在 Registry。
- 上一版本配置可恢复。
- Migration 兼容策略明确。
- 发布前备份存在。

### 20.1 触发条件

出现以下任一情况停止扩大流量并回滚：

- API 大面积 5xx。
- 登录不可用。
- 任务无法启动。
- Worker 大面积失败。
- 数据错误写入。
- Migration 破坏核心业务。
- 跨用户数据风险。
- Evidence/Artifact 严重丢失。
- 资源失控导致服务不稳定。

### 20.2 回滚方式

```text
停止新版本
↓
切回上一稳定 image tag/digest
↓
恢复兼容配置
↓
必要时按预先验证方案处理 DB
↓
重新运行 Smoke Test
```

不可逆 Migration 不允许临场猜测式回滚，必须在发布前设计前向修复/兼容窗口。

---

## 21. 服务器变更纪律

禁止：

```text
SSH 进去 vim 改 Python
进入容器改 Vue 构建产物
手工 pip install
手工 npm install
直接改数据库让它“先跑”
```

线上 Bug 必须：

```text
Git 分支
→ 修复
→ 测试
→ PR/CI
→ 新镜像
→ Staging
→ 新版本部署
```

服务器只接受**已版本化的产物和配置变更**。

---

## 22. Production Release Checklist

发布前：

- [ ] `app.example.com` DNS 解析正确。
- [ ] HTTPS 证书有效且自动续期配置正常。
- [ ] 云安全组只保留必要公网端口。
- [ ] SSH 密码登录关闭、root 登录关闭、密钥登录正常。
- [ ] Fail2ban 或等价 SSH 防护正常。
- [ ] 宿主机磁盘、Docker、PostgreSQL、MinIO 容量正常。
- [ ] Staging RC 通过。
- [ ] Git tag 已创建。
- [ ] Commit SHA 已记录。
- [ ] Web/API/Worker image digest 已记录。
- [ ] Migration version 已记录。
- [ ] Production Secrets 完整。
- [ ] PostgreSQL 备份完成。
- [ ] 对象存储备份/恢复点完成。
- [ ] 上一稳定镜像仍可拉取。
- [ ] 磁盘/CPU/内存容量正常。
- [ ] 回滚 Runbook 可用。

发布后：

- [ ] Liveness 通过。
- [ ] Readiness 通过。
- [ ] 登录通过。
- [ ] 小任务创建通过。
- [ ] Workflow/Worker 通过。
- [ ] 数据写入通过。
- [ ] Evidence/CSV 通过。
- [ ] 日志无 Secrets。
- [ ] 监控无 P0/P1 异常。
- [ ] 发布记录归档。

任何 P0/P1 项失败，Production Release 不得标记成功。

---

## 23. 第一版不做的部署复杂化

除非后续容量事实证明必要，第一版禁止提前引入：

- Kubernetes。
- 多节点 Temporal Cluster 自运维。
- Service Mesh。
- Consul/复杂服务发现。
- 独立消息中间件仅为了“微服务感”。
- 独立认证微服务。
- 多区域部署。
- 自动跨云容灾。

优先把单服务器 Docker Compose 做到稳定、可恢复、可迁移。

---

## 24. 后续扩容顺序

出现真实瓶颈后，优先按以下顺序拆分：

```text
1. Browser Worker → 独立服务器
2. HTTP/Scrapy Worker → 水平扩容
3. PostgreSQL → 托管数据库/独立主机
4. MinIO → 云 S3
5. Temporal → 托管或独立集群
6. API → 多实例 + Load Balancer
```

这个顺序不属于当前必须实施项，只有监控数据证明瓶颈后才执行。


---

## 25. Production Bugfix Default Deployment（已上线功能 Bug 修复默认闭环）

> 用户已确认的长期规则：已上线 Production 功能的 Bug 修复，默认必须部署到 Production。
> 部署到 Production 是 Bug Fix 本身的一部分，不是可选的后续步骤。

### 25.1 默认目标

```text
默认目标服务器：47.238.145.24
默认用户验收入口：https://app.kairos.ac.cn/
```

### 25.2 正常路径

```text
fix branch
→ scoped tests
→ PR / CI
→ main
→ immutable GHCR images
→ Staging pull
→ Staging smoke
→ patch release
→ Production pull
→ migration (如有)
→ compose
→ health / readiness
→ user-facing smoke
```

### 25.3 硬性验收

修复后的最终网页必须来自最新修复 Release。禁止出现：

```text
Local fixed
但
app.kairos.ac.cn 仍运行旧版本
```

然后宣布完成。发布身份必须记录：

```text
Git commit SHA
Release tag
Web image digest
API image digest
Worker image digest
Migration head
Deploy timestamp
```

禁止使用 `latest` 作为唯一部署身份。

### 25.4 GitHub 网络阻断时的降级

若 `github.com` 不可达（无法 Push / 开 PR / 触发 CI）：

- 以本地完整等价门禁代替 CI（全量测试 / ruff / mypy / vue-tsc / lint / build / secret scan）；
- 使用 `infra/scripts/registry-push.sh` 本地构建不可变镜像并推 GHCR；
- 仍必须完成 Staging → Staging Smoke → Production → Production Smoke 全链路；
- 报告明确把 `PR / CI PASS` 标记为 **PENDING（网络阻断）**，不得伪称已通过；
- 网络恢复后必须补 Push / PR 闭环。

### 25.5 不可绕过的 Gate

默认部署不得绕过以下安全 Gate；任一失败必须 BLOCK：

```text
Production health 不通过
Staging smoke 不通过
Secret 缺失
Migration 风险不明确
需要新的付费基础设施
需要产品决策
部署可能造成不可逆数据损坏
```
