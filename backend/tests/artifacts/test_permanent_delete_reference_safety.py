"""M-15 永久删除引用安全（TEST F）：删除 A 不破坏 B 共享对象（D-072）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.artifacts.deletion import DeletionService
from app.domain.errors import DomainError
from app.domain.models import Artifact, PageSnapshot, Task
from app.domain.repository import TaskRepository


def _shared_snapshot(db, user, task, ref):
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
    )
    db.add(s)
    db.flush()
    return s


def _shared_artifact(db, user, task, ref):
    a = Artifact(
        user_id=user.id,
        task_id=task.id,
        artifact_type="csv",
        content_hash="h",
        storage_ref=ref,
        status="ready",
    )
    db.add(a)
    db.flush()
    return a


@pytest.mark.asyncio
async def test_permanent_delete_user_a_keeps_user_b_blob(
    db, user_a, user_b, task_a, storage
) -> None:
    ref = "snapshots/u1/h/tool.html"
    _shared_snapshot(db, user_a, task_a, ref)
    # 软删除 A
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "DELETED"
    task.deleted_at = datetime.now(UTC)
    db.commit()
    # 用户 B 另一任务引用同一 ref（共享对象场景）
    t2 = Task(user_id=user_b.id, title="b", state="DELETED", deleted_at=datetime.now(UTC))
    db.add(t2)
    db.flush()
    _shared_snapshot(db, user_b, t2, ref)
    await storage.put(ref, b"<html>x</html>", "text/html")

    svc = DeletionService(db, storage)
    manifest = await svc.permanent_delete(user_id=user_a.id, task_id=task_a.id, confirmed=True)
    # B 的任务与快照行仍在，共享对象保留
    assert TaskRepository(db).get_owned(user_b.id, t2.id) is not None
    assert await storage.exists(ref) is True
    assert ref in manifest.objects_kept


@pytest.mark.asyncio
async def test_permanent_delete_last_ref_removes_object(db, user_a, task_a, storage) -> None:
    ref = "artifacts/u1/csv/h.csv"
    _shared_artifact(db, user_a, task_a, ref)
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "DELETED"
    task.deleted_at = datetime.now(UTC)
    db.commit()
    await storage.put(ref, b"a,b\r\n", "text/csv")

    svc = DeletionService(db, storage)
    manifest = await svc.permanent_delete(user_id=user_a.id, task_id=task_a.id, confirmed=True)
    assert await storage.exists(ref) is False  # 最后一个引用消失才物理删除
    assert manifest.objects_removed == [ref]


@pytest.mark.asyncio
async def test_permanent_delete_requires_confirm_and_deleted(db, user_a, task_a, storage) -> None:
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "DELETED"
    task.deleted_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(DomainError):
        await DeletionService(db, storage).permanent_delete(
            user_id=user_a.id, task_id=task_a.id, confirmed=False
        )
