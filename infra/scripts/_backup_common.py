"""Backup bundle 通用逻辑（manifest / flock / disk preflight / retention）。

供 infra/scripts/backup.sh 调用；纯函数可被 backend/tests/ops/test_backup.py import。
Windows 开发机无 fcntl，lock 相关用例用 pytest.importorskip("fcntl") 跳过。
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, Iterator

try:  # pragma: no cover - Windows 开发机无 fcntl
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


@dataclass
class BackupManifest:
    """BackupManifest：可追溯的备份元数据，绝不包含明文 Secret（只记录引用）。"""

    backup_id: str
    environment: str
    timestamp: str
    git_sha: str
    migration_head: str
    postgres: dict[str, Any]
    objects: dict[str, Any]
    config: dict[str, Any]
    secrets: dict[str, Any]
    status: str = "complete"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "environment": self.environment,
            "timestamp": self.timestamp,
            "git_sha": self.git_sha,
            "migration_head": self.migration_head,
            "postgres": self.postgres,
            "objects": self.objects,
            "config": self.config,
            "secrets": self.secrets,
            "status": self.status,
        }


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_MANIFEST_FORBIDDEN = (
    "api_key=",
    "secret_key=",
    "password=",
    "authorization: bearer",
    "credential_master_key=",
    "session_secret=",
    "begin rsa private key",
    "begin openssh private key",
    "begin ec private key",
)


def write_manifest(path: str, manifest: BackupManifest) -> None:
    data = manifest.to_dict()
    serialized = json.dumps(data, ensure_ascii=False).lower()
    for marker in _MANIFEST_FORBIDDEN:
        assert marker not in serialized, f"manifest must not carry secrets ({marker})"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


@contextlib.contextmanager
def acquire_lock(lock_path: str) -> Iterator[None]:
    """非阻塞 flock。拿不到锁立即抛 RuntimeError，防止两个 backup 同时跑。"""
    if fcntl is None:
        raise RuntimeError("flock unsupported on this platform")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        raise RuntimeError(f"backup already running (lock held): {lock_path}") from None
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def disk_preflight(paths: list[str], min_free_mb: int) -> list[str]:
    """返回不足容量的路径列表（空列表=通过）。"""
    problems: list[str] = []
    for p in paths:
        usage = shutil.disk_usage(p)
        free_mb = usage.free // (1 << 20)
        if free_mb < min_free_mb:
            problems.append(f"{p} free={free_mb}MB < {min_free_mb}MB")
    return problems


def apply_retention(backup_root: str, keep_days: int) -> list[str]:
    """删除早于保留周期的旧 backup 目录，返回被删目录名列表。"""
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=keep_days)
    removed: list[str] = []
    for name in sorted(os.listdir(backup_root)):
        full = os.path.join(backup_root, name)
        if not os.path.isdir(full):
            continue
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(full), datetime.UTC)
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(full, ignore_errors=True)
            removed.append(name)
    return removed
