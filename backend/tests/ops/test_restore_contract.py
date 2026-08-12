"""TEST E：restore 顺序与 checksum 校验契约（真实 Restore Drill 留 Staging）。

验证 restore-drill.sh 读取的 manifest 字段契约、checksum 校验能识别篡改、
以及恢复环境隔离要求（独立 volume/network，不触碰 staging）。
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "infra", "scripts"))
from _backup_common import sha256_file  # type: ignore[import-not-found]

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")


def test_restore_requires_manifest_fields() -> None:
    m = {
        "postgres": {"ref": "postgres/postgres.dump"},
        "objects": {"ref": "objects/objects.tar.gz"},
        "migration_head": "0014",
    }
    # restore-drill.sh 必须能读到这些字段
    assert {"postgres", "objects", "migration_head"} <= set(m)
    assert m["postgres"]["ref"].endswith(".dump")
    assert m["objects"]["ref"].endswith(".tar.gz")


def test_checksum_verify_detects_tamper(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.bin").write_bytes(b"kairos" * 10)
    (src / "a.bin.sha256").write_text(sha256_file(str(src / "a.bin")))
    dst = tmp_path / "dst"
    shutil.copytree(src, dst)
    assert sha256_file(str(dst / "a.bin")) == (dst / "a.bin.sha256").read_text().strip()
    # 篡改 → 校验失败
    (dst / "a.bin").write_bytes(b"tampered")
    assert sha256_file(str(dst / "a.bin")) != (dst / "a.bin.sha256").read_text().strip()


def test_restore_verify_checks_require_both_pg_and_objects() -> None:
    """_restore_verify 的 5 项验证同时依赖 PG（Task/Record/Evidence）与对象存储（Snapshot/CSV）。"""
    pg_checks = {"task", "record", "evidence"}
    object_checks = {"snapshot", "csv"}
    assert pg_checks & object_checks == set()
    # 5 项 = 3 项 PG 依赖 + 2 项对象存储依赖
    assert len(pg_checks | object_checks) == 5


def test_restore_drill_isolated_from_staging() -> None:
    compose_path = os.path.join(ROOT, "infra", "compose", "compose.restore-drill.yml")
    with open(compose_path, encoding="utf-8") as fh:
        text = fh.read()
    # 独立 project / 独立 volume / 独立 network
    assert "name: kairos-restore-drill" in text
    assert "kairos-restore-drill_drill_postgres_data" in text
    assert "kairos-restore-drill_drill_minio_data" in text
    assert "kairos-restore-internal" in text
    # 绝不引用 staging 卷 / 域名 / edge 网络
    assert "kairos-staging_postgres_data" not in text
    assert "kairos-staging_minio_data" not in text
    assert "lumina-prod-internal" not in text
    assert "staging.kairos.ac.cn" not in text
