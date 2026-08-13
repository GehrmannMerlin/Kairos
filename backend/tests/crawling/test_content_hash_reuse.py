"""内容 Hash 测试（六十二）：相同内容抓两次 → hash 一致、Blob 不重复上传、observation 链保留。"""
from __future__ import annotations

import hashlib

import pytest
from app.crawling.fetch_executor import FetchNodeExecutor
from app.crawling.http_fetch import SafeFetchHttp
from app.crawling.repository import PageSnapshotRepository
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.http import DiscoveryHttp
from app.discovery.models import FrontierState
from app.discovery.robots import RobotsCache
from tests.crawling.conftest import SITE_HOST, FakeFetchTransport, make_unit, seed_ready

BODY = b"<html><body><p>Identical Static Page</p></body></html>"


@pytest.mark.asyncio
async def test_identical_content_fetch_twice_reuses_blob_and_keeps_audit(ctx, storage) -> None:
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/same": {"status": 200, "body": BODY},
        }
    )
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    executor = FetchNodeExecutor(
        db, http=http, robots=robots, storage=storage, retry_base_seconds=0
    )
    seed_ready(ctx, "http://fixture.test/same")
    url_hash = ""

    # 第一次抓取
    r1 = await executor.execute(make_unit(run, 1, "fetch"))
    assert r1.status == "OK" and r1.committed_refs["fetched"] == 1
    snapshots1 = PageSnapshotRepository(db).list_for_task(user.id, task.id)
    url_hash = snapshots1[0].url_resource_id

    # 第二次抓取（同一 URL 重新 READY_FOR_FETCH）
    from app.domain.models import URLResource

    frontier = UrlFrontierRepository(db)
    row = db.get(URLResource, url_hash)
    frontier.mark_state(
        user_id=user.id,
        task_id=row.task_id,
        url_hash=row.url_hash,
        state=FrontierState.READY_FOR_FETCH,
    )
    r2 = await executor.execute(make_unit(run, 2, "fetch"))
    assert r2.status == "OK" and r2.committed_refs["fetched"] == 1

    snapshots = PageSnapshotRepository(db).list_for_task(user.id, task.id)
    assert len(snapshots) == 2  # 保留两次抓取 observation
    # content hash 一致，与 body sha256 相同
    assert snapshots[0].content_hash == snapshots[1].content_hash
    assert snapshots[0].content_hash == hashlib.sha256(BODY).hexdigest()
    # Blob 只上传一次（不复制对象）；第二条 observation 复用同一 storage_ref
    assert storage.put_calls == 1
    assert snapshots[0].storage_ref == snapshots[1].storage_ref
    # observation 链：version 1 → 2，prior 关联；captured_at 记录“何时再次抓取”
    assert [s.snapshot_version for s in snapshots] == [1, 2]
    assert snapshots[1].prior_snapshot_id == snapshots[0].id
    assert snapshots[1].captured_at is not None
