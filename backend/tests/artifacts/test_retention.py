"""M-15 Retention（TEST G）：过期未引用删除 / 过期被 Evidence 引用保护 / 未到期保留。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.artifacts.retention import RetentionService
from app.domain.models import FieldEvidence, PageSnapshot


def _snap(db, user, task, *, ref, age_days):
    s = PageSnapshot(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        content_hash="h",
        storage_ref=ref,
        mime_type="text/html",
        tool="http",
        tool_version="1",
        final_url="http://x",
        captured_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db.add(s)
    db.flush()
    return s


@pytest.mark.asyncio
async def test_retention_three_cases(db, user_a, task_a, storage) -> None:
    expired_unref = _snap(db, user_a, task_a, ref="snapshots/u1/1/a.html", age_days=100)
    protected = _snap(db, user_a, task_a, ref="snapshots/u1/2/b.html", age_days=100)
    fresh = _snap(db, user_a, task_a, ref="snapshots/u1/3/c.html", age_days=5)
    # protected 被 FieldEvidence 引用
    ev = FieldEvidence(
        user_id=user_a.id,
        task_id=task_a.id,
        record_id=1,
        field_name="标题",
        snapshot_id=protected.id,
        raw_snippet="原文片段",
        source_locator="div.x",
    )
    db.add(ev)
    db.flush()
    await storage.put(expired_unref.storage_ref, b"<html>a</html>", "text/html")
    await storage.put(protected.storage_ref, b"<html>b</html>", "text/html")
    await storage.put(fresh.storage_ref, b"<html>c</html>", "text/html")

    svc = RetentionService(db, storage, retention_days=30)
    result = await svc.run(dry_run=False)
    assert result.scanned == 3
    assert result.deleted == 1
    assert result.protected == 1
    assert result.failed == 0
    assert await storage.exists(expired_unref.storage_ref) is False
    assert await storage.exists(protected.storage_ref) is True
    assert await storage.exists(fresh.storage_ref) is True
    # FieldEvidence 最小片段仍存在（与 raw object 解耦）
    assert ev.raw_snippet == "原文片段"
    assert ev.source_locator == "div.x"


@pytest.mark.asyncio
async def test_retention_dry_run_no_delete(db, user_a, task_a, storage) -> None:
    snap = _snap(db, user_a, task_a, ref="snapshots/u1/4/d.html", age_days=100)
    await storage.put(snap.storage_ref, b"<html>d</html>", "text/html")
    svc = RetentionService(db, storage, retention_days=30)
    result = await svc.run(dry_run=True)
    assert result.dry_run is True
    assert result.deleted == 0
    assert await storage.exists(snap.storage_ref) is True
