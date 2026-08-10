# DEPLOY-GATE-1 执行记录：真实服务器 Staging 上线

状态：IN_PROGRESS
负责人/Agent：Claude Code — 2026-08-10
Deploy Branch：`ci/deploy-gate-1-staging`
Baseline M-04 SHA：`cb4823117652450c822ef6834847ed3e6d93c5dc`
目标环境：staging

> 说明：本记录随 DEPLOY-GATE-1 执行逐步回填证据。真实服务器审计、DNS 现状与共享 nginx 拓扑已在 Task 1 固化。最终状态在全部 Smoke 完成后更新为 PASS / BLOCKED。

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

- deploy user：PENDING
- SSH public-key fingerprint：PENDING
- PermitRootLogin no / PasswordAuthentication no / Fail2ban：PENDING
- 新会话验证：PENDING

### 服务器基线与 Secrets（Task 3）

- /srv/kairos 目录：PENDING
- /srv/kairos/env/staging.env（600）：PENDING（幂等保留，Master Key 稳定）

### Repository 部署产物（Task 4）

- compose base/staging/override：PENDING
- reverse-proxy vhost：PENDING
- 脚本：PENDING

### 镜像与部署（Task 5）

- Git SHA：PENDING
- image tags/digests：PENDING
- compose project：PENDING
- 容器状态：PENDING

### Migration + Gate Smoke（Task 6）

- Migration revision：PENDING（预期 0004）
- health live/ready：PENDING
- auth / ownership / credential security：PENDING
- Temporal / M-04 checkpoint：PENDING

### DNS + HTTPS（Task 7）

- DNS：PENDING
- HTTPS：PENDING

### Restart / Rollback（Task 8）

- persistence / restart：PENDING
- rollback readiness：PENDING
- release manifest：PENDING
- secret scan：PENDING

## 5. Final Status

- DEPLOY-GATE-1：PENDING
- M-04：PENDING（达到 DEPLOYED）
- M-05：PENDING（达到 UNBLOCKED，但不开始）
