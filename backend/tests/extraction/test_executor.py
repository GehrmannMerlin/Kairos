"""M-11 ExtractNodeExecutor: bounded batch, per-snapshot commit, failure ledger.

覆盖任务书 §31：batch 隔离、预算返回 MORE_PENDING、CancelledError 不吞成普通失败、
成功小批次独立提交（一页失败不拖死全部）。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.domain.models import FieldEvidence, PageSnapshot, Record
from app.extraction.contracts import (
    ExtractionCandidate,
    ExtractionResult,
    ExtractionSettings,
    ExtractorMethod,
)
from app.extraction.executor import ExtractNodeExecutor
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.repository import ExtractionRepository

from .conftest import seed_snapshot

_HTML = b"<html><body><p>fixture</p></body></html>"


def _candidate(field_name: str = "公司名", value: str = "样例公司") -> ExtractionCandidate:
    return ExtractionCandidate(
        field_name=field_name,
        raw_value=value,
        normalized_value=value,
        value_type="text",
        method=ExtractorMethod.JSON_LD,
        confidence=0.9,
        extractor_version="m11.1",
    )


def _result(
    snapshot_id: int, candidates: list[ExtractionCandidate] | None = None
) -> ExtractionResult:
    return ExtractionResult(
        snapshot_id=snapshot_id,
        schema_version="m11.1",
        extractor_type="ladder",
        extractor_version="m11.1",
        candidates=candidates or [_candidate()],
        unresolved_fields=[],
        issues=[],
        technical_metadata={"llm_invocations": 0, "llm_retries": 0},
    )


class _FakePipeline:
    """按调用顺序返回预设结果；异常按索引抛出，用于失败隔离测试。"""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.calls = 0

    async def run(self, snapshot: PageSnapshot, spec_payload: dict, *, user_id: int):
        idx = self.calls
        self.calls += 1
        outcome = self._results[idx]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _fake_pipeline(results: list[Any]) -> ExtractionPipeline:
    """构造与 ExtractNodeExecutor.pipeline 契约一致的 fake（mypy 名义类型需要 cast）。"""
    return cast(ExtractionPipeline, _FakePipeline(results))


def _unit(ctx) -> ExecutionUnit:
    return ExecutionUnit(
        run_id=ctx["run"].id,
        index=3,
        unit_type="extract",
        input_fingerprint="f" * 64,
        node_id="extract-1",
        node_type="extract",
    )


@pytest.mark.asyncio
async def test_executor_processes_bounded_batch_and_returns_more_pending(ctx, storage):
    db = ctx["db"]
    run = ctx["run"]
    snap_ids = [seed_snapshot(ctx, _HTML, storage) for _ in range(7)]
    pipeline = _fake_pipeline([_result(sid) for sid in snap_ids])
    executor = ExtractNodeExecutor(
        db, storage, pipeline=pipeline, settings=ExtractionSettings(extract_batch_size=5)
    )

    first = await executor.execute(_unit(ctx))
    assert first.status == "MORE_PENDING"
    assert first.committed_refs["extracted"] == 5
    assert first.committed_refs["failed"] == 0
    assert first.committed_refs["remaining"] == 2
    assert first.committed_refs["batch_identity"] == f"extract-{run.id}-3-{snap_ids[0]}"
    assert db.query(Record).count() == 5  # 每快照独立提交，本批已持久化

    second = await executor.execute(_unit(ctx))
    assert second.status == "OK"
    assert second.committed_refs["extracted"] == 2
    assert second.committed_refs["remaining"] == 0
    assert db.query(Record).count() == 7
    assert db.query(FieldEvidence).count() >= 7


@pytest.mark.asyncio
async def test_executor_commits_snapshot_independently(ctx, storage):
    """页面 2 失败不能拖死页面 1/3：已成功快照各自提交，失败快照进账本。"""
    db = ctx["db"]
    snap_ids = [seed_snapshot(ctx, _HTML, storage) for _ in range(3)]
    pipeline = _fake_pipeline(
        [
            _result(snap_ids[0]),
            RuntimeError("page 2 network failure"),
            _result(snap_ids[2]),
        ]
    )
    executor = ExtractNodeExecutor(
        db, storage, pipeline=pipeline, settings=ExtractionSettings(extract_batch_size=5)
    )

    result = await executor.execute(_unit(ctx))

    assert result.status == "OK"
    assert result.committed_refs["extracted"] == 2
    assert result.committed_refs["failed"] == 1
    assert db.query(Record).count() == 2  # 不是 0 records
    assert db.query(FieldEvidence).count() == 2
    failed_snapshot = db.get(PageSnapshot, snap_ids[1])
    assert failed_snapshot is not None
    assert failed_snapshot.extraction_status == "failed"


@pytest.mark.asyncio
async def test_executor_marks_terminal_failure_snapshot(ctx, storage):
    """全阶梯 0 candidates → 页面级合法失败，进账本后不再被 pending_snapshots 返回。"""
    db = ctx["db"]
    sid = seed_snapshot(ctx, _HTML, storage)
    empty = ExtractionResult(
        snapshot_id=sid,
        schema_version="m11.1",
        extractor_type="ladder",
        extractor_version="m11.1",
        candidates=[],
        unresolved_fields=["公司名", "官网"],
        issues=[],
        technical_metadata={"llm_invocations": 1, "llm_retries": 1},
    )
    executor = ExtractNodeExecutor(
        db, storage, pipeline=_fake_pipeline([empty]), settings=ExtractionSettings()
    )

    result = await executor.execute(_unit(ctx))

    assert result.status == "OK"
    assert result.committed_refs["extracted"] == 0
    assert result.committed_refs["failed"] == 1
    snapshot = db.get(PageSnapshot, sid)
    assert snapshot is not None
    assert snapshot.extraction_status == "failed"
    repo = ExtractionRepository(db)
    assert repo.pending_snapshots(user_id=ctx["user"].id, task_id=ctx["task"].id) == []


@pytest.mark.asyncio
async def test_executor_activity_budget_returns_more_pending(ctx, storage, monkeypatch):
    """预算到点后提前返回 MORE_PENDING，不再启动新快照。"""
    import app.extraction.executor as executor_module

    db = ctx["db"]
    snap_ids = [seed_snapshot(ctx, _HTML, storage) for _ in range(3)]
    pipeline = _fake_pipeline([_result(sid) for sid in snap_ids])
    timestamps = iter([0.0, 0.5, 1.5])  # started=0; snapshot1 check 0.5; snapshot2 check 1.5>1
    monkeypatch.setattr(executor_module, "perf_counter", lambda: next(timestamps))
    executor = ExtractNodeExecutor(
        db,
        storage,
        pipeline=pipeline,
        settings=ExtractionSettings(extract_batch_size=5, extract_activity_budget_seconds=1),
    )

    result = await executor.execute(_unit(ctx))

    assert result.status == "MORE_PENDING"
    assert result.committed_refs["extracted"] == 1  # 只有快照 1 被处理
    assert result.committed_refs["remaining"] == 2
    assert db.query(Record).count() == 1


@pytest.mark.asyncio
async def test_executor_propagates_cancelled_error_and_keeps_committed(ctx, storage):
    """真实 CancelledError（BaseException）不被 except Exception 吞掉：向上传播，已提交不丢。"""
    db = ctx["db"]
    snap_ids = [seed_snapshot(ctx, _HTML, storage) for _ in range(3)]
    pipeline = _fake_pipeline(
        [
            _result(snap_ids[0]),
            asyncio.CancelledError(),
            _result(snap_ids[2]),
        ]
    )
    executor = ExtractNodeExecutor(
        db, storage, pipeline=pipeline, settings=ExtractionSettings(extract_batch_size=5)
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(_unit(ctx))

    assert db.query(Record).count() == 1  # 快照 1 已提交，不丢


@pytest.mark.asyncio
async def test_cancelled_error_is_not_classified_as_provider_timeout():
    assert ExtractionPipeline._is_provider_timeout(asyncio.CancelledError()) is False
    from app.providers.errors import ProviderTimeoutError, TimeoutPhase

    assert (
        ExtractionPipeline._is_provider_timeout(ProviderTimeoutError(phase=TimeoutPhase.OVERALL))
        is True
    )
