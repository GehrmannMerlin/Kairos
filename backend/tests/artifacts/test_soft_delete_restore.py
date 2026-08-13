"""M-15 软删除/恢复：非运行任务可删可恢复，运行任务必须 cancel（D-025/D-065）。"""

from __future__ import annotations

from app.domain.errors import IllegalTransitionError
from app.domain.repository import TaskRepository
from app.domain.task_commands import TaskCommandService


def test_soft_delete_hides_and_restores(db, user_a, task_a) -> None:

    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "COMPLETED"  # 模拟终态任务
    db.commit()
    svc = TaskCommandService(db)
    r = svc.delete_task(
        user_id=user_a.id, task_id=task_a.id, expected_version=task.version
    )
    assert r.state == "DELETED"
    # normal list 隐藏
    assert TaskRepository(db).list_by_user(user_a.id) == []
    # deleted view 可见
    deleted = TaskRepository(db).list_deleted(user_a.id)
    assert [t.id for t in deleted] == [task_a.id]
    # restore → 回到删除前终态（不破坏 Run execution facts）
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    r2 = svc.restore_task(
        user_id=user_a.id, task_id=task_a.id, expected_version=task.version
    )
    assert r2.state == "COMPLETED"
    assert TaskRepository(db).get_owned(user_a.id, task_a.id).deleted_at is None


def test_running_task_cannot_delete(db, user_a, task_a) -> None:
    task = TaskRepository(db).get_owned(user_a.id, task_a.id)
    task.state = "RUNNING"
    task.version += 1
    db.commit()
    svc = TaskCommandService(db)
    try:
        svc.delete_task(
            user_id=user_a.id, task_id=task_a.id, expected_version=task.version
        )
        raise AssertionError("running delete should be rejected")
    except IllegalTransitionError:
        pass
