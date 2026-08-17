"""install_extraction_executors 注册 + NODE_EXECUTOR 绑定（M-08 seam）。"""

from __future__ import annotations

import asyncio

from app.activities.execution_seam import ExecutionUnit
from app.extraction.executors import install_extraction_executors
from app.plan.executors import NODE_EXECUTORS
from app.plan.nodes import NodeType


def test_install_registers_extract_and_normalize():
    install_extraction_executors()
    assert NodeType.EXTRACT in NODE_EXECUTORS
    assert NodeType.NORMALIZE in NODE_EXECUTORS
    assert callable(NODE_EXECUTORS[NodeType.EXTRACT])
    assert callable(NODE_EXECUTORS[NodeType.NORMALIZE])


class _FakeResolver:
    def __init__(self, audit: dict | None = None) -> None:
        self._audit = audit or {"provider": "deepseek", "model": "v4-flash"}

    def resolve_for_run(self, run):  # noqa: ARG002
        return None, None, self._audit


class _FakeLlmPipeline:
    def __init__(self, llm_invocations: int, with_candidate: bool = True) -> None:
        self._llm_invocations = llm_invocations
        self._with_candidate = with_candidate

    async def run(self, snapshot, spec_payload, *, user_id):  # noqa: ARG002
        from app.extraction.contracts import ExtractionCandidate, ExtractionResult, ExtractorMethod

        candidates = []
        if self._with_candidate:
            candidates = [
                ExtractionCandidate(
                    field_name="公司名",
                    raw_value="Acme",
                    normalized_value="Acme",
                    value_type="text",
                    method=ExtractorMethod.LLM,
                    confidence=0.8,
                    extractor_version="m11.1",
                )
            ]
        return ExtractionResult(
            snapshot_id=snapshot.id,
            schema_version="m11.1",
            extractor_type="ladder",
            extractor_version="m11.1",
            candidates=candidates,
            unresolved_fields=[],
            issues=[],
            duration_ms=10,
            technical_metadata={"llm_invocations": self._llm_invocations, "user_id": user_id},
        )


def _run_extract(ctx, storage, pipeline, resolver):
    from app.extraction.executor import ExtractNodeExecutor
    from tests.extraction.conftest import seed_snapshot

    db = ctx["db"]
    snap_id = seed_snapshot(ctx, b"<html><body>acme</body></html>", storage)
    unit = ExecutionUnit(
        run_id=ctx["run"].id,
        index=1,
        unit_type="extract",
        input_fingerprint="fp",
        node_type="extract",
    )
    executor = ExtractNodeExecutor(db, storage, pipeline=pipeline, model_resolver=resolver)
    result = asyncio.run(executor.execute(unit))
    return db, snap_id, result


def test_extract_emits_llm_fallback_used_when_llm_used(ctx, storage):
    from app.domain.models import DomainEvent

    db, _, result = _run_extract(ctx, storage, _FakeLlmPipeline(llm_invocations=1), _FakeResolver())
    assert result.status == "OK"
    events = list(
        db.query(DomainEvent).filter(DomainEvent.event_type == "extraction.llm_fallback_used")
    )
    assert len(events) == 1
    payload = events[0].payload
    assert payload["model"] == "v4-flash"
    assert payload["provider"] == "deepseek"
    # 安全摘要：绝不携带 secret / prompt 正文
    for secret_key in ("api_key", "credential", "prompt", "readable_text"):
        assert secret_key not in payload


def test_extract_does_not_emit_llm_event_for_deterministic_only(ctx, storage):
    from app.domain.models import DomainEvent

    db, _, result = _run_extract(ctx, storage, _FakeLlmPipeline(llm_invocations=0), _FakeResolver())
    assert result.status == "OK"
    events = list(
        db.query(DomainEvent).filter(DomainEvent.event_type == "extraction.llm_fallback_used")
    )
    assert events == []
