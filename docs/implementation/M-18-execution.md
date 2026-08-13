# M-18 模块执行记录

状态：**DONE**（2026-08-13）— Production Release v0.1.0 上线，DEPLOY-GATE-5 全 PASS
负责人/Agent：Claude Code
基线：M-18 起点 SHA `98d44038b0196d`（M-17 DONE HEAD）；Release SHA `89ccf66c1677`（镜像构建源）
分支：`feature/M-18-production-release` → main（pushed：NO，网络阻断，见 §9）
依赖模块：M-01～M-17 全部 DONE；DEPLOY-GATE-1～4 通过

## 1. 模块目标
完成第一版正式 Production Release：隔离的 Production 环境初始化、不可变 release、正式域名/HTTPS、真实部署、最小业务链 Smoke、长期外部 off-site 备份、回滚就绪，DEPLOY-GATE-5 全部 PASS。

## 2. 实施计划
- 使用 superpowers:writing-plans 真实调用；Plan 文件 `docs/superpowers/plans/2026-08-12-m18-production-release.md`（8 macro task）。
- **PROJECT SELF-APPROVAL：CHECK 1-16 全 PASS。PLAN SELF-APPROVAL：PASS（21 项）。**
- 使用 superpowers:executing-plans 自动执行（Inline，用户预授权）。

## 3. 外部前置（用户提供）
- **External OSS Backup Target**：`/srv/kairos/env/backup-target.env`（0600）。tiny probe（upload→download→checksum→delete）全 PASS → `PRODUCTION_OFFSITE_BACKUP=READY`。
- **DNS**：`app.kairos.ac.cn A 47.238.145.24` 用户创建并生效；`staging.kairos.ac.cn` 未改动。

## 4. Release
- **Version**：`v0.1.0`（首个 tag，SemVer 0.x.y）
- **Git SHA**：`89ccf66c1677`（app 镜像构建源）
- **Image digests**：
  - web：`sha256:1d673df66164b3fe3b054b739fe44c7f3c3aff588f9dd3bf64b48fc0831aee8b`
  - api：`sha256:bb2311f0250ea99c3597d8c80db8f6a8fe981bfa0308b0340e18d9027f688d6b`
  - worker：`sha256:bb2311f0250ea99c3597d8c80db8f6a8fe981bfa0308b0340e18d9027f688d6b`
- **Migration**：`0014`（干净空库 `alembic upgrade head`）
- **Manifest**：`/srv/kairos/releases/manifest-v0.1.0.json`（版本/SHA/digest/migration/backup_id/rollback_target，无 Secret）

## 5. Production 环境（与 staging 严格隔离）
- 独立 compose project `kairos-production`、独立 internal network、独立 volume。
- DB：`kairos_production`（全新空库，不含 staging 数据）。
- ObjectStorage：bucket `kairos-production`。
- Temporal：namespace `kairos-production`。
- Secrets：`/srv/kairos/env/production.env`（0600，openssl rand 全新生成，不复用 staging）。
- 内部服务（postgres/temporal/minio/otel/worker）零 host 端口；公网仅 22/80/443（`NETWORK_BOUNDARY: PASS`）。
- Worker：`kairos-production` namespace + `kairos-*` task queues，与 staging 完全隔离。

## 6. 域名 / HTTPS
- `https://app.kairos.ac.cn`（单域名同源）。
- Let's Encrypt 证书签发 + auto-renew；HTTP→HTTPS 301；HSTS `max-age=31536000`；trusted chain（CN=app.kairos.ac.cn, issuer=Let's Encrypt YE2）。
- production vhost 代理到 `kairos-production-api-1`/`kairos-production-web-1`（**修复：不再串到 staging 独占的 kairos-api/kairos-web**）。

## 7. Production Smoke（极小真实业务链，SPECIFIED_SOURCE）
- 目标：`https://example.com/`（公开、静态、robots 允许、1 页）。
- 链路：register/login → DeepSeek ModelConfig（真实连接测试 AVAILABLE）→ create task → understand → spec-confirm → plan → workflow → 轮询。
- **结果**：`SMOKE_RESULT=PASS total=20`。
  - TASK_ID=3, RUN_ID=2, WORKFLOW_ID=task-workflow-3
  - terminal state=`PARTIALLY_COMPLETED`（可解释）
  - Records=1, Execution view 可读, Quality 可读, Completion card 可读
  - CSV export + download（`text/csv; charset=utf-8`），artifact_id=1, content_hash=`638b76d92f6c65c213c4ef84ef6f8de4f08e4f5607851e912061927a9d9d5d6f`
- DeepSeek key 经服务器 `/srv/kairos/env/smoke-deepseek.key`（0600）传入，未回显/未入库。

## 8. Backup（真实执行，smoke 后立即）
- **Backup ID**：`production-20260813-012736-89ccf66c1677`（git_sha=89ccf66c1677, migration_head=0014）
- PG dump / ObjectStorage（`kairos-production_minio_data`）/ config / secrets（AES-256 加密）/ manifest / checksums 全 PASS。
- **External off-site copy**：`OFF_SERVER_S3_COPY=PASS`（bucket `kairos-prod-backup-2026`，src/dst sha256 一致）。
- Backup manifest 无明文 secret（secrets 字段仅 `{encrypted:true, ref, sha256, key_location}`）。

## 9. Rollback / Ops / Git
- **Rollback readiness**：`ROLLBACK_READY=PASS`（FIRST_PRODUCTION_RELEASE，down 保留 volume，migration 0014 兼容）。
- **Ops health**：`{"status":"PASS"}`（production 容器 running、disk 62%、api live/ready ok、latest backup 存在）。
- **M-18 acceptance**：`RESULT=PASS total=15`（配置校验/脱敏/隔离/worker/容量/manifest/DB 指标）。
- **Git**：feature 分支 10 个 commit；main 已 ff 到 89ccf66；**pushed：NO**（github.com HTTPS 被网络阻断，SSH key 是 Aurora deploy key 无 Kairos 权限）。tag `v0.1.0` 已本地创建。待网络可达后：`git push origin main && git push origin v0.1.0`。

## 10. 明确未做 / 状态
- 未做：完整 load test、long soak、完整渗透测试、Search Provider smoke（复用 Golden B）、DEFERRED-DYNAMIC-E2E-01、新增产品功能。
- **GitHub push 延后**（网络阻断，用户授权先部署）。
- DEFERRED-DYNAMIC-E2E-01 保持 DEFERRED，不阻塞 Production Release。

## 11. 完成结论
**M-18 = DONE**。**DEPLOY-GATE-5 = PASS**。**Production = RELEASED**（`https://app.kairos.ac.cn`）。**Staging = HEALTHY**（`https://staging.kairos.ac.cn`）。
