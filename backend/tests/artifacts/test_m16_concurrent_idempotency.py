"""M-16 并发幂等回归（TEST 7）：两个相同 ExportRequest 近同时执行。

故意让两个导出都 miss `find_ready`（模拟并发窗口）→ 第二个 insert 命中
Artifacts(request_fingerprint) 部分唯一索引 → IntegrityError → 回滚复用获胜方。
最终：同一 content identity、单个 artifact row、单个 blob。
"""

from __future__ import annotations

import pytest
from app.artifacts.contracts import ExportRequest
from app.artifacts.repository import ArtifactRepository
from app.artifacts.service import ArtifactService
from app.domain.models import Artifact, Record


def _seed(db, user, task) -> None:
    r = Record(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        partition="passed",
        payload={"标题": "并发幂等"},
    )
    db.add(r)
    db.flush()


@pytest.mark.asyncio
async def test_concurrent_same_export_single_artifact_and_blob(
    db, user_a, task_a, storage, monkeypatch
) -> None:
    _seed(db, user_a, task_a)
    service = ArtifactService(db, storage)
    req = ExportRequest(export_type="formal", scope="all")

    real_find = ArtifactRepository(db).find_ready
    calls = {"n": 0}

    def racy_find(self, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return None  # 两个并发请求都越过 find_ready 检查（竞态窗口）
        return real_find(**kwargs)  # IntegrityError 后重新查找获胜方

    monkeypatch.setattr(ArtifactRepository, "find_ready", racy_find)

    ref_a = await service.export(user_id=user_a.id, task_id=task_a.id, request=req)
    ref_b = await service.export(user_id=user_a.id, task_id=task_a.id, request=req)

    assert ref_a.content_hash == ref_b.content_hash  # 同一 content identity
    rows = db.query(Artifact).all()
    assert len(rows) == 1  # 部分唯一索引兜底：无 duplicate artifact row
    assert len(storage.objects) == 1  # 无 duplicate blob
