"""M-15 ArtifactService：幂等导出 + 数据变化生成新 Artifact（D-016/D-060）。"""

from __future__ import annotations

import pytest
from app.artifacts.contracts import ExportRequest
from app.artifacts.service import ArtifactService
from app.domain.models import Record


def _seed(db, user, task, *, partition="passed", value="x"):
    r = Record(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        partition=partition,
        payload={"标题": value},
    )
    db.add(r)
    db.flush()
    return r


@pytest.mark.asyncio
async def test_same_export_reuses_artifact(db, user_a, task_a, storage) -> None:
    _seed(db, user_a, task_a)
    svc = ArtifactService(db, storage)
    req = ExportRequest(export_type="formal", scope="all")
    ref1 = await svc.export(user_id=user_a.id, task_id=task_a.id, request=req)
    ref2 = await svc.export(user_id=user_a.id, task_id=task_a.id, request=req)
    assert ref1.artifact_id == ref2.artifact_id
    assert ref1.content_hash == ref2.content_hash
    assert len(storage.objects) == 1  # blob 不重复


@pytest.mark.asyncio
async def test_data_change_creates_new_artifact(db, user_a, task_a, storage) -> None:
    from app.review.contracts import RecordReviewCommand, ReviewAction
    from app.review.service import ReviewService

    r = _seed(db, user_a, task_a, partition="needs_review", value="a")
    svc = ArtifactService(db, storage)
    ref1 = await svc.export(
        user_id=user_a.id,
        task_id=task_a.id,
        request=ExportRequest(export_type="review", scope="all"),
    )
    ReviewService(db).execute(
        user_id=user_a.id,
        record_id=r.id,
        cmd=RecordReviewCommand(
            action=ReviewAction.APPROVE, expected_data_version=r.data_version
        ),
    )
    ref2 = await svc.export(
        user_id=user_a.id,
        task_id=task_a.id,
        request=ExportRequest(export_type="formal", scope="all"),
    )
    assert ref2.artifact_id != ref1.artifact_id


@pytest.mark.asyncio
async def test_formal_export_passed_only(db, user_a, task_a, storage) -> None:
    from app.domain.models import Record as R

    _seed(db, user_a, task_a, partition="passed", value="p")
    db.add(
        R(
            user_id=user_a.id,
            task_id=task_a.id,
            spec_version=1,
            partition="needs_review",
            review_type="missing_required",
            review_reason="missing_required",
            payload={"标题": "r"},
        )
    )
    db.flush()
    svc = ArtifactService(db, storage)
    ref = await svc.export(
        user_id=user_a.id,
        task_id=task_a.id,
        request=ExportRequest(export_type="formal", scope="all"),
    )
    assert ref.row_count == 1  # 正式 CSV 只含 PASSED
