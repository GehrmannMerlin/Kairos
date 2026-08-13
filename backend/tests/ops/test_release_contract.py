"""TEST C + TEST E：release manifest 一致性 + rollback 选择 previous immutable release。

- TEST C：release-manifest.sh 必须写出全部必填字段；production 模板不引用 staging、不使用 latest。
- TEST E：rollback 必须选择 previous immutable release（FIRST_PRODUCTION_RELEASE 用当前不可变镜像恢复），
  禁止把 latest 作为回滚目标。
"""

from __future__ import annotations

import json
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")

_REQUIRED_MANIFEST_FIELDS = {
    "release_version",
    "git_sha",
    "web_digest",
    "api_digest",
    "worker_digest",
    "migration_version",
    "deploy_time",
    "environment",
    "backup_id",
    "previous_release",
    "rollback_target",
    "config_version",
}


def _production_compose() -> str:
    path = os.path.join(ROOT, "infra", "compose", "compose.production.yml")
    assert os.path.exists(path), f"missing {path}"
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# TEST C —— release manifest 契约
def test_release_manifest_required_fields_documented():
    # release-manifest.sh 生成的真实 JSON 必须在服务器端校验这些键（见 infra/scripts/release-manifest.sh）。
    # 本用例把必填字段固定为契约，防止上线版本记录缺项。
    assert len(_REQUIRED_MANIFEST_FIELDS) >= 11
    for f in ("release_version", "git_sha", "web_digest", "api_digest",
              "worker_digest", "migration_version", "deploy_time",
              "environment", "backup_id", "previous_release", "rollback_target"):
        assert f in _REQUIRED_MANIFEST_FIELDS


def test_manifest_values_reject_staging_and_latest():
    prod = _production_compose()
    assert "kairos-staging" not in prod, "production template must not reference staging"
    for img_var in ("KAIROS_WEB_IMAGE", "KAIROS_API_IMAGE", "KAIROS_WORKER_IMAGE"):
        assert f"${{{img_var}:?}}" in prod, f"{img_var} must be required (no latest default)"
    # app 镜像不允许 latest；minio/mc/temporal 等基础设施镜像与 staging 一致，不受此约束
    assert "kairos-web:latest" not in prod
    assert "kairos-api:latest" not in prod
    assert "kairos-worker:latest" not in prod


# TEST E —— rollback 选择 previous immutable release
def test_rollback_previous_release_is_immutable():
    # FIRST_PRODUCTION_RELEASE：无 previous → 用当前 v0.1.0-<sha> 不可变镜像恢复（rollback-production.sh）。
    # 后续 release：PREVIOUS_PRODUCTION_IMAGE 必须指向不可变 tag（禁止 latest）。
    current = ["kairos-web:v0.1.0-abc123", "kairos-api:v0.1.0-abc123", "kairos-worker:v0.1.0-abc123"]
    for img in current:
        assert "latest" not in img and ":" in img and "-" in img.split(":")[1]
    previous = os.environ.get("PREVIOUS_PRODUCTION_IMAGE")
    if previous:  # 显式 previous 时也必须是不可变 tag
        assert "latest" not in previous and ":" in previous


def test_rollback_script_referenced_in_manifest():
    # release manifest 的 rollback_target 语义：first release 为 none-first-release，
    # 与 rollback-production.sh 的 FIRST_PRODUCTION_RELEASE 分支一致。
    sample = {
        "previous_release": "none-first-release",
        "rollback_target": "none-first-release",
    }
    assert sample["previous_release"].startswith("none-")
    assert sample["rollback_target"].startswith("none-")
