# DEPLOY-GATE-1 执行记录：真实服务器 Staging 上线

状态：PASS
负责人/Agent：Claude Code — 2026-08-10
Deploy Branch：`ci/deploy-gate-1-staging`
Baseline M-04 SHA：`cb4823117652450c822ef6834847ed3e6d93c5dc`
目标环境：staging

> 说明：本记录随 DEPLOY-GATE-1 执行逐步回填证据。真实服务器审计、DNS 现状与共享 nginx 拓扑在 Task 1 固化；DNS 由用户在 Alibaba DNS Zone 添加 `staging A 47.238.145.24` 后，HTTPS 证书签发 + 共享 nginx vhost 激活完成，全量 Gate Smoke 通过，最终状态 PASS。

## 1. 目标

把 M-01～M-04 系统以 `kairos-staging` 独立 Compose 项目上线到 `https://staging.kairos.ac.cn`（真实服务器 `47.238.145.24`），完成 Migration / Health / Auth / Ownership / Credential Security / Temporal / M-04 Checkpoint / Persistence / Restart / Rollback Readiness 全部 Gate Smoke，达成 `DEPLOY-GATE-1 = PASS`、`M-04 = DEPLOYED`、`M-05 = UNBLOCKED`（本轮不开始 M-05）。

## 2. 服务器审计（Task 1 固化）

- Server IP：`47.238.145.24`
- 服务器非空：托管 lumina / stellaris / aurora-preview 三套线上业务。
- OS：Ubuntu 24.04.4 LTS；Arch：x86_64（amd64）；CPU：8；RAM：14Gi。
- Disk：99G，93% used，约 7.5G 可用（全流程监控，谨慎部署）。
- Docker：29.6.2；Compose：v5.3.1（均已存在，不重装）。
- 共享反向代理：`lumina-prod-nginx-1` 独占 80/443，网络 `lumina-prod-internal`，`ReadonlyRootfs=false`。
- 既有 vhost 注入模式：每个项目 vhost `.conf` 经 compose override 只读 bind-mount 进共享 nginx（aurora/stellaris 同款）。
- SSH 现状：`PasswordAuthentication no` 已为基线；`PermitRootLogin yes` 需按安全顺序谨慎关闭；ecs-user 具备 passwordless sudo + docker 组。
- 防火墙：ufw inactive，iptables INPUT ACCEPT（按部署规范只保留 22/80/443，PostgreSQL/Temporal/MinIO/OTel 内部端口不暴露公网）。

## 3. DNS 现状（Task 1 固化）

- Zone `kairos.ac.cn` 存在，NS = dns11/dns12.hichina.com（Alibaba Cloud DNS）。
- `staging.kairos.ac.cn` 当前无 A 记录；`kairos.ac.cn` 也无 A 记录。
- 本机与服务器均无 aliyun CLI、无 Alibaba 凭据、无 browser automation 授权 → DNS control-plane 不可自动 → 记为 `BLOCKED_DNS_AUTH` 候选。
- 目标记录：`staging A 47.238.145.24`。

## 4. 待回填证据

### SSH Bootstrap（Task 2）

- deploy user：PASS — `deploy` 已创建，加入 docker 组，最小 sudo（仅 certbot + systemctl restart docker，`/etc/sudoers.d/kairos-deploy`）
- 新 Key：`~/.ssh/kairos_staging_deploy_rsa`（RSA 4096，comment `kairos-staging-deploy`，repo 外）
- SSH public-key fingerprint：`SHA256:RTsDg3In9jnzaGQWa/JRnwJ6CvX8TfI0i3vlIp1owyc`
- 新会话验证：PASS — `ssh -i kairos_staging_deploy_rsa deploy@47.238.145.24` 成功
- SSH 加固：PASS — `PermitRootLogin no`、`PasswordAuthentication no`（基线已 no）、`PubkeyAuthentication yes`、`MaxAuthTries 3`；`sshd -T` 确认生效
- 加固备份：`/etc/ssh/sshd_config.kairos.bak-20260810-174009`
- Fail2ban：PASS — active（22 保持公网开放，仅 key 认证）
- 既有 ecs-user 通道复验：PASS（未锁死既有入口）

### 服务器基线与 Secrets（Task 3）

- /srv/kairos 目录：PASS — `{compose,env,data/staging,backups/staging,scripts,releases,deploy/nginx/conf.d}` owner=deploy；`env` 700
- Docker：29.6.2（已存在，不重装）；时区 Asia/Shanghai
- Docker 日志轮转：daemon.json 为空，不改共享 daemon（会重启影响线上 lumina/stellaris/aurora）；改为在 kairos compose 服务级配置 log rotation（Task 4 实现）
- /srv/kairos/env/staging.env（600）：PASS — 已生成（POSTGRES_*/MINIO_*/KAIROS_SESSION_SECRET/KAIROS_CREDENTIAL_MASTER_KEY/k1）
- Master Key 幂等保留：PASS — 重跑不重建（MASTER_KEY_STABLE_PASS），M-03 解密稳定

### Repository 部署产物（Task 4）

- compose base/staging/override：PASS — `compose.base.yml`（base，服务级 log rotation，无宿主端口）+ `compose.staging.yml`（env_file 可插拔、web/api join `lumina-prod-internal`、container_name `kairos-api/kairos-web`）+ `compose.staging.override.yml`（共享 nginx vhost 注入）
- reverse-proxy vhost：PASS — `infra/reverse-proxy/zz-kairos-staging-tls.conf`（staging.kairos.ac.cn，/api/events/ 关 proxy_buffering 预留 SSE）
- 脚本：PASS — deploy/migrate/smoke/rollback-staging.sh（`bash -n` 全过）
- 本地校验：PASS — `docker compose config -q`（base 与 base+staging，env_file 用 `KAIROS_STAGING_ENV_FILE` 可插拔）
- Commit：`c27cd89`（+ 基线 `89c787b`）

### 镜像与部署（Task 5）

- Git SHA：`0b8a42c31f8d`（含 httpx runtime 修复；首次 `c27cd89cdd6d` 因缺 httpx 启动失败）
- 部署发现并修复 M-03 runtime 缺陷：`httpx` 只在 dev extras，生产镜像 `pip install .` 无 httpx → `fix(provider)` commit `0b8a42c`
- image tags：`kairos-web:staging-0b8a42c31f8d` / `kairos-api:staging-0b8a42c31f8d` / `kairos-worker:staging-0b8a42c31f8d`
- image digests：
  - web `sha256:7dd4be85f31aac5f1fbaf1b7935e66f004038e251ae6c0c0957db24d504ff0c5`
  - api `sha256:c859cdf5c9487943c8fbe020723b7a3afe349178c0cd9ac090bdcae7a28dc84d`
  - worker `sha256:b3fe3aa81be775c89c4b85b12e38f8100f0d14a64f0b3212452de0e7e2205ece`
- compose project：`kairos-staging`（网络 `kairos-staging-internal` + web/api join `lumina-prod-internal`）
- 容器状态：PASS — postgres/minio/temporal/api/web healthy；worker up；minio-init exited 0；migrate 已达 head（见 Task 6）
- 传输方式：docker save → SSH → docker load（本地 buildx linux/amd64，不在服务器构建）

### Migration + Gate Smoke（Task 6）

- Migration revision：PASS — `alembic_version = 0004`（当前 head，干净 staging DB，24 张表）
- health live：PASS — `/api/health/live` 200 ok
- health ready：PASS — `/api/health/ready` 200，postgresql/temporal/object_storage 全 ok
- Auth Smoke：PASS — A/B register 201、login 200、session `me` 200、logout 204、logout 后 me 401（Gate Test User A/B）
- Ownership：PASS — B 读 A 的 Task 触发 404 policy（get_owned），无泄漏；A 可读自身
- Credential Security：PASS — `GATE_TEST_SECRET` 存为密文（ciphertext 无明文），decrypt roundtrip OK，revoke 完成；**发现并修复 master key 生成长度缺陷**（32→64 hex）
- Temporal：PASS — M-01 `smoke_workflow` → Activity → PG row + MinIO object 双向读回 OK
- M-04 Checkpoint：PASS — submit 同事务写 state=QUEUED + event + outbox；commit_checkpoint → replay 复用（count=1）
- Secret Leak 扫描：PASS — api/worker/temporal/postgres 日志、DB 可见字段、/srv/kairos 均无 `GATE_TEST_SECRET`
- 附加修复：`gen-staging-env.sh` 脚本入库（master key 64 hex，幂等保留）

### DNS + HTTPS（Task 7）

- DNS：PASS — 用户已在 Alibaba DNS Zone 添加 `staging A 47.238.145.24`；公网 resolver（223.5.5.5 / 8.8.8.8）与权威 NS（dns.alidns.com cd=1）均返回 `47.238.145.24`
- HTTPS 证书：PASS — certbot webroot 签发 `staging.kairos.ac.cn`（Let's Encrypt，CN=staging.kairos.ac.cn，有效期 2026-08-10 → 2026-11-08，自动续期已配置）
- 共享 nginx vhost：PASS — 采用两阶段激活（共享 nginx 手动管理，无 compose labels，以 `docker cp` + `nginx -t` + reload 非破坏性注入，不影响 lumina/stellaris/aurora）：
  1. 先注入 HTTP-only ACME 块（default_server 对未匹配 Host 返回 444，必须先建挑战块）→ 签发证书
  2. 再注入完整 TLS vhost `zz-kairos-staging-tls.conf`，移除 HTTP-only 块 → `nginx -t` PASS → reload
- 既有 vhost 复验：lumina.ac.cn / stellaris.ac.cn 均 200（未受影响）
- HTTPS 验证：`https://staging.kairos.ac.cn/` 200（Kairos SPA，title=Kairos）；`/api/health/live` 200；`/api/health/ready` 200；HTTP→HTTPS 301；TLS 默认 CA 链可信（非 self-signed）
- 共享 nginx 注入说明：`docker cp` 方式为容器重建后需重新注入；`infra/compose/compose.staging.override.yml` 记录了 recreate 时保留 aurora/stellaris/kairos 全部挂载的约束

### Restart / Rollback（Task 8）

- persistence：PASS — DB rows（smoke_probe/users/tasks）与 MinIO object 在 restart 后仍在
- restart recovery：PASS — api/worker restart 后 ready 200，Temporal 重连，worker 重新监听 queue
- rollback readiness：PASS — FIRST_STAGING_RELEASE；`down --remove-orphans`（无 -v，保留 named volumes）→ `up -d --wait` 以当前不可变镜像恢复，数据仍在（smoke_probe=1, users=11, tasks=4, alembic=0004）
- release manifest：PASS — `/srv/kairos/releases/manifest-0b8a42c31f8d.json`（Git SHA / image tags+digests / migration / deploy time / domain；无 Secrets）
- secret scan：PASS — GATE_TEST_SECRET 在 api/worker/temporal/postgres 日志、DB 可见字段、/srv/kairos 均无匹配
- 脚本修复：rollback-staging.sh 改为按创建时间解析最新不可变镜像（SHA 字典序无意义）；deploy-staging.sh 注入 image tag + otel 配置路径；smoke-staging.sh 修正 auth(confirm_password+Secure cookie)/ownership(get_owned)/credential(vault)/M-04 checkpoint 签名
- 测试数据清理：PASS — 删除全部 `@kairos.test` Gate 测试用户及其 domain/credential/session 行（FK 顺序），保留 smoke_probe 作为持久化证据；清理后栈仍 healthy
- 公网 HTTPS Auth Smoke：PASS — register 201 / login 200 / me 200 / logout 204 / me-after-logout 401（Secure+HttpOnly cookie 经真实 HTTPS 正常流转）
- 最终 HTTPS 复核：web `/` 200（Kairos SPA）、`/api/health/live` 200、`/api/health/ready` 200、lumina/stellaris 200；测试用户已清理

## 5. Final Status

- DEPLOY-GATE-1：**PASS**（DNS 已解析 / HTTPS 真实可信 / 全量 Gate Smoke 通过）
- M-04：**DEPLOYED**（staging 已部署并验证：migration 0004 / state machine / event+outbox / checkpoint replay / owner isolation / persistence / restart / rollback 恢复）
- M-05：**UNBLOCKED**（但本轮不开始，等待下一步指令）
