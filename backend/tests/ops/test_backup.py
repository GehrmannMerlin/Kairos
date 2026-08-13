"""TEST C+D：backup manifest / flock lock / disk preflight / retention（M-17）。

真实 Restore Drill 留 Staging；这里用纯函数与小 fixture 验证 backup 工具契约。
Windows 开发机无 fcntl → lock 用例跳过（Staging 真机验证 lock）。
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "infra", "scripts"))
from _backup_common import (  # type: ignore[import-not-found]
    BackupManifest,
    acquire_lock,
    apply_retention,
    disk_preflight,
    sha256_file,
    write_manifest,
)

_HAS_FCNTL = importlib.util.find_spec("fcntl") is not None


@pytest.fixture
def manifest() -> BackupManifest:
    return BackupManifest(
        backup_id="staging-20260812-000000-abcd",
        environment="staging",
        timestamp="2026-08-12T00:00:00+00:00",
        git_sha="71b926b5235f",
        migration_head="0014",
        postgres={"ref": "postgres/postgres.dump", "sha256": "a" * 64, "size": 123},
        objects={"ref": "objects/objects.tar.gz", "sha256": "b" * 64, "size": 456},
        config={"ref": "config/config.tar.gz", "sha256": "c" * 64, "size": 78},
        secrets={
            "encrypted": True,
            "ref": "secrets/secrets.env.enc",
            "key_location": "/srv/kairos/env/backup.key",
        },
    )


def test_manifest_roundtrip(tmp_path, manifest):
    p = tmp_path / "manifest.json"
    write_manifest(str(p), manifest)
    data = json.loads(p.read_text())
    assert data["backup_id"] == manifest.backup_id
    assert data["status"] == "complete"
    assert "api_key" not in p.read_text().lower()


def test_manifest_never_contains_plaintext_secret(tmp_path):
    bad = "credential_master_key=M17_SECRET_CANARY"
    m = BackupManifest(
        "x", "staging", "t", "sha", "0014",
        {"ref": "p", "sha256": "a" * 64, "size": 1},
        {"ref": "o", "sha256": "b" * 64, "size": 1},
        {"ref": "c", "sha256": "c" * 64, "size": 1},
        {"encrypted": True, "ref": bad, "key_location": "k"},
    )
    with pytest.raises(AssertionError):
        write_manifest(str(tmp_path / "m.json"), m)


def test_sha256_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"kairos" * 100)
    assert len(sha256_file(str(p))) == 64


@pytest.mark.skipif(not _HAS_FCNTL, reason="flock unsupported on this platform")
def test_lock_blocks_concurrent_backup(tmp_path):
    lock = str(tmp_path / "backup.lock")
    with acquire_lock(lock):  # noqa: SIM117 - 故意持锁再尝试二次获取
        with pytest.raises(RuntimeError, match="backup already running"):
            with acquire_lock(lock):
                pass


@pytest.mark.skipif(not _HAS_FCNTL, reason="flock unsupported on this platform")
def test_lock_released_after_block(tmp_path):
    lock = str(tmp_path / "backup.lock")
    with acquire_lock(lock):
        pass
    with acquire_lock(lock):  # 可再获取 = 释放成功
        pass


def test_disk_preflight_flags_low_free(tmp_path):
    problems = disk_preflight([str(tmp_path)], 1 << 30)
    assert any("free=" in p for p in problems)


def test_retention_removes_old_only(tmp_path):
    old = tmp_path / "staging-old"
    old.mkdir()
    t = datetime.datetime.now().timestamp() - (30 * 86400)
    os.utime(old, (t, t))
    fresh = tmp_path / "staging-new"
    fresh.mkdir()
    removed = apply_retention(str(tmp_path), keep_days=14)
    assert old.name in removed and not old.exists()
    assert fresh.exists()


def test_backup_bundle_has_checksums_on_server_layout(tmp_path):
    """模拟服务器 backup 目录：每个产物文件都有对应 .sha256。"""
    bundle = tmp_path / "staging-20260812-000000-abcd"
    for sub in ("postgres", "objects", "config", "secrets"):
        (bundle / sub).mkdir(parents=True)
    (bundle / "postgres" / "postgres.dump").write_bytes(b"dump" * 10)
    (bundle / "objects" / "objects.tar.gz").write_bytes(b"tar" * 10)
    (bundle / "config" / "config.tar.gz").write_bytes(b"cfg" * 10)
    for rel in ("postgres/postgres.dump", "objects/objects.tar.gz", "config/config.tar.gz"):
        p = bundle / rel
        (bundle / f"{rel}.sha256").write_text(sha256_file(str(p)))
    for rel in ("postgres/postgres.dump", "objects/objects.tar.gz", "config/config.tar.gz"):
        sha = (bundle / f"{rel}.sha256").read_text().strip()
        assert sha256_file(str(bundle / rel)) == sha
