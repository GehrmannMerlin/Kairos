"""Strictly allowlisted structured events for inference and plan lifecycles."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Final

LIFECYCLE_EVENTS: Final = (
    "inference.started",
    "inference.attempt_finished",
    "inference.failed",
    "plan.validation_finished",
    "plan.persisted",
    "plan.workflow_start_finished",
)

_ALLOWED_FIELDS: Final = frozenset(
    {
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
)
_SCALAR_TYPES = (str, int, float, bool)
_LOGGER = logging.getLogger("kairos.inference_lifecycle")


class _LifecycleJsonFormatter(logging.Formatter):
    """Render only the registered event name and allowlisted lifecycle fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {"event_name": record.event_name}  # type: ignore[attr-defined]
        for name in sorted(_ALLOWED_FIELDS):
            if hasattr(record, name):
                value = getattr(record, name)
                payload[name] = list(value) if isinstance(value, tuple) else value
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _configure_lifecycle_logger() -> None:
    if not any(getattr(handler, "_kairos_lifecycle", False) for handler in _LOGGER.handlers):
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(_LifecycleJsonFormatter())
        handler._kairos_lifecycle = True  # type: ignore[attr-defined]
        _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    # The dedicated formatter is the security boundary; do not duplicate the raw record upstream.
    _LOGGER.propagate = False


_configure_lifecycle_logger()


def _current_correlation_id() -> str | None:
    from app.observability.context import get_log_context

    context = get_log_context()
    contextual = context.get("correlation_id") or context.get("trace_id")
    if isinstance(contextual, str) and contextual:
        return contextual
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        span_context = span.get_span_context() if span.is_recording() else None
        if span_context is not None and span_context.is_valid:
            return f"{span_context.trace_id:032x}"
    except Exception:
        return None
    return None


def _normalize_field(name: str, value: object) -> object:
    if value is None or isinstance(value, _SCALAR_TYPES):
        return value
    if name == "issue_codes" and isinstance(value, Sequence) and not isinstance(value, str):
        if not all(isinstance(item, str) for item in value):
            raise TypeError("issue_codes must contain only strings")
        return tuple(value)
    raise TypeError(f"telemetry field {name!r} has an unsafe value type")


def emit_lifecycle_event(event_name: str, **fields: object) -> None:
    """Emit one structured event only after every field passes the allowlist.

    Prompt text, serialized responses, credentials and graphs cannot be supplied because
    unknown keys and container-shaped values are rejected before the logging call.
    """

    if event_name not in LIFECYCLE_EVENTS:
        raise ValueError(f"lifecycle event {event_name!r} is not registered")
    if "correlation_id" not in fields:
        correlation_id = _current_correlation_id()
        if correlation_id is not None:
            fields["correlation_id"] = correlation_id
    unknown = fields.keys() - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"telemetry fields are not allowlisted: {sorted(unknown)}")
    safe_fields = {name: _normalize_field(name, value) for name, value in fields.items()}
    _LOGGER.info(event_name, extra={"event_name": event_name, **safe_fields})
