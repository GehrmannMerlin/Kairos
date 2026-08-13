"""PageSnapshot 持久化测试：content-addressable 复用、observation 链、owner 隔离、immutable。"""
from __future__ import annotations

import pytest
from app.crawling.repository import PageSnapshotRepository
from app.crawling.snapshot import PageSnapshotService
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import DiscoverySource
from app.domain.models import URLResource


def _url_resource(ctx) -> int:
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    frontier = UrlFrontierRepository(db)
    url_hash, _ = frontier.upsert_discovery(
        task_id=task.id,
        user_id=user.id,
        run_id=run.id,
        spec_version=1,
        raw_url="https://fixture.example/page",
        source=DiscoverySource.USER_SEED,
    )
    row = (
        db.query(URLResource)
        .filter(URLResource.task_id == task.id, URLResource.url_hash == url_hash)
        .first()
    )
    assert row is not None
    return row.id


def _service(ctx, storage):
    return PageSnapshotService(
        ctx["db"],
        storage,
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        run_id=ctx["run"].id,
        spec_version=1,
    )


@pytest.mark.asyncio
async def test_same_content_reuses_blob_and_keeps_observation_chain(ctx, storage) -> None:
    db = ctx["db"]
    service = _service(ctx, storage)
    url_resource_id = _url_resource(ctx)
    body = b"<html>static</html>"

    ref1 = await service.commit_raw(
        body=body,
        url_resource_id=url_resource_id,
        tool="http",
        tool_version="1.0",
        source_url="https://fixture.example/page",
        final_url="https://fixture.example/page",
        http_status=200,
        content_type="text/html",
        content_length=len(body),
        duration_ms=10,
    )
    ref2 = await service.commit_raw(
        body=body,
        url_resource_id=url_resource_id,
        tool="http",
        tool_version="1.0",
        source_url="https://fixture.example/page",
        final_url="https://fixture.example/page",
        http_status=200,
        content_type="text/html",
        content_length=len(body),
        duration_ms=10,
    )

    # 相同内容 → 同一 content_hash；Blob 只上传一次（不复制对象）
    assert ref1.content_hash == ref2.content_hash
    assert storage.put_calls == 1
    # 保留两次 observation（“何时再次抓取”审计）
    rows = PageSnapshotRepository(db).find_by_url_resource(
        user_id=ctx["user"].id, url_resource_id=url_resource_id
    )
    assert len(rows) == 2
    assert [r.snapshot_version for r in rows] == [1, 2]
    assert rows[1].prior_snapshot_id == rows[0].id


@pytest.mark.asyncio
async def test_snapshot_owner_isolation(ctx, storage) -> None:
    service = _service(ctx, storage)
    ref = await service.commit_raw(
        body=b"<html>private</html>",
        url_resource_id=None,
        tool="http",
        tool_version="1.0",
        source_url="https://fixture.example/private",
        final_url="https://fixture.example/private",
        http_status=200,
        content_type="text/html",
        content_length=0,
        duration_ms=1,
    )
    # user B 按 id 查询 A 的 snapshot → None（owner-safe 404 语义）
    repo = PageSnapshotRepository(ctx["db"])
    other_user = 9999
    assert repo.find_by_id(other_user, ref.snapshot_id) is None


@pytest.mark.asyncio
async def test_snapshot_immutable_no_overwrite(ctx, storage) -> None:
    db = ctx["db"]
    service = _service(ctx, storage)
    url_resource_id = _url_resource(ctx)
    ref = await service.commit_raw(
        body=b"<html>v1</html>",
        url_resource_id=url_resource_id,
        tool="http",
        tool_version="1.0",
        source_url="https://fixture.example/page",
        final_url="https://fixture.example/page",
        http_status=200,
        content_type="text/html",
        content_length=0,
        duration_ms=1,
    )
    # 再次 commit 不同内容 → 新 observation，不覆盖历史行
    ref2 = await service.commit_raw(
        body=b"<html>v2</html>",
        url_resource_id=url_resource_id,
        tool="http",
        tool_version="1.0",
        source_url="https://fixture.example/page",
        final_url="https://fixture.example/page",
        http_status=200,
        content_type="text/html",
        content_length=0,
        duration_ms=1,
    )
    assert ref.snapshot_id != ref2.snapshot_id
    old = PageSnapshotRepository(db).find_by_id(ctx["user"].id, ref.snapshot_id)
    assert old is not None
    assert old.content_hash == PageSnapshotService.content_hash(b"<html>v1</html>")
