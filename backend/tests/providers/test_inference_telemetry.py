"""Allowlisted inference and plan lifecycle telemetry."""

from __future__ import annotations

import json
import logging
import subprocess
import sys

import pytest
from app.agents.plan_generator import PlanInput
from app.agents.plan_service import PlanGenerationService
from app.config import Settings
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft
from app.providers import errors, inference_telemetry
from app.providers.inference import ModelInferenceClient
from app.providers.inference_policy import InferenceIntent
from app.providers.inference_telemetry import LIFECYCLE_EVENTS, emit_lifecycle_event
from app.providers.protocol import ResolvedModel
from app.providers.transport import HttpResponse

SECRET = "sk-secret-never-log"
PROMPT = "private prompt must never be logged"
FORBIDDEN_KEY_FRAGMENTS = (
    "authorization",
    "api_key",
    "credential",
    "prompt",
    "messages",
    "response_body",
    "graph",
)


class _SequencedHttpClient:
    def __init__(self, effects: list[Exception | HttpResponse]) -> None:
        self.effects = list(effects)

    async def request(self, **kwargs) -> HttpResponse:
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _StaticPlanAgent:
    async def generate(self, inp, resolved, *, api_key=None) -> PlanGraphDraft:
        return PlanGraphDraft.model_validate(
            {
                "schema_version": "m08.1",
                "task_id": 1,
                "spec_version": 1,
                "task_type": "SPECIFIED_SOURCE",
                "nodes": [],
                "edges": [],
            }
        )


def _client(http) -> ModelInferenceClient:
    settings = Settings(
        provider_inference_timeout_seconds=1,
        capacity_default_retry_max_attempts=1,
        provider_throttle_min_interval_seconds=0,
        provider_throttle_max_burst=100,
    )
    return ModelInferenceClient(
        intent=InferenceIntent.PLAN_STRUCTURED,
        settings=settings,
        http=http,
        timeout_seconds=1,
        retry_base_delay_seconds=0,
    )


def _resolved() -> ResolvedModel:
    return ResolvedModel("deepseek", "deepseek-chat", "https://example.invalid/v1", 17)


def _event_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if hasattr(record, "event_name")]


@pytest.fixture
def lifecycle_caplog(caplog: pytest.LogCaptureFixture):
    logger = logging.getLogger("kairos.inference_lifecycle")
    caplog.handler.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


def _assert_safe(records: list[logging.LogRecord]) -> None:
    for record in records:
        payload = {
            key: value
            for key, value in record.__dict__.items()
            if key == "event_name"
            or key
            in {
                "provider_type",
                "model",
                "intent",
                "timeout_phase",
                "attempt_number",
                "elapsed_ms",
                "response_status",
                "plan_version",
                "issue_codes",
                "run_state",
                "request_id",
                "correlation_id",
            }
        }
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        assert SECRET.lower() not in serialized
        assert PROMPT.lower() not in serialized
        for fragment in FORBIDDEN_KEY_FRAGMENTS:
            assert fragment not in payload


def test_helper_emits_exact_event_names_and_rejects_unknown_fields(
    lifecycle_caplog: pytest.LogCaptureFixture,
) -> None:
    for event_name in LIFECYCLE_EVENTS:
        emit_lifecycle_event(event_name, elapsed_ms=1)

    assert [record.event_name for record in _event_records(lifecycle_caplog)] == list(
        LIFECYCLE_EVENTS
    )
    with pytest.raises(ValueError, match="not allowlisted"):
        emit_lifecycle_event("inference.started", prompt=PROMPT)


def test_lifecycle_event_is_rendered_to_stderr_as_safe_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.providers.inference_telemetry import emit_lifecycle_event; "
                "emit_lifecycle_event('plan.validation_finished', elapsed_ms=7, "
                "response_status='VALID', "
                "issue_codes=('RESOURCE_EDGE_INCOMPATIBLE',))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    rendered = result.stderr.strip()
    assert rendered
    payload = json.loads(rendered.splitlines()[-1])
    assert payload == {
        "elapsed_ms": 7,
        "event_name": "plan.validation_finished",
        "issue_codes": ["RESOURCE_EDGE_INCOMPATIBLE"],
        "response_status": "VALID",
    }
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert SECRET.lower() not in serialized
    assert PROMPT.lower() not in serialized


def test_lifecycle_event_adds_the_active_correlation_id(
    lifecycle_caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        inference_telemetry,
        "_current_correlation_id",
        lambda: "0123456789abcdef0123456789abcdef",
        raising=False,
    )

    emit_lifecycle_event("inference.started", intent="PLAN_STRUCTURED")

    records = _event_records(lifecycle_caplog)
    assert records[-1].correlation_id == "0123456789abcdef0123456789abcdef"


@pytest.mark.asyncio
async def test_inference_success_and_failure_emit_safe_attempt_events(
    lifecycle_caplog: pytest.LogCaptureFixture,
) -> None:
    success = _client(
        _SequencedHttpClient(
            [HttpResponse(200, {"choices": [{"message": {"content": '{"ok":true}'}}]})]
        )
    )

    await success.generate(resolved=_resolved(), api_key=SECRET, system=PROMPT, user=PROMPT)

    failure = _client(
        _SequencedHttpClient([errors.ProviderTimeoutError(phase=errors.TimeoutPhase.READ)])
    )
    with pytest.raises(errors.ProviderTimeoutError):
        await failure.generate(resolved=_resolved(), api_key=SECRET, system=PROMPT, user=PROMPT)

    records = _event_records(lifecycle_caplog)
    assert [record.event_name for record in records] == [
        "inference.started",
        "inference.attempt_finished",
        "inference.started",
        "inference.attempt_finished",
        "inference.failed",
    ]
    assert records[-1].timeout_phase == "read"
    _assert_safe(records)


@pytest.mark.asyncio
async def test_plan_validation_emits_issue_codes_without_graph(
    lifecycle_caplog: pytest.LogCaptureFixture,
) -> None:
    service = PlanGenerationService(registry=NodeRegistry(), agent=_StaticPlanAgent())
    inp = PlanInput(
        task_id=1,
        spec_version=1,
        spec_payload={},
        task_type=TaskType.SPECIFIED_SOURCE,
        registry_metadata=[],
        execution_constraints={"has_search_provider": False},
    )

    await service._run_with_graph({}, inp, _resolved(), api_key=SECRET)

    records = _event_records(lifecycle_caplog)
    assert [record.event_name for record in records] == ["plan.validation_finished"]
    assert isinstance(records[0].issue_codes, tuple)
    _assert_safe(records)
