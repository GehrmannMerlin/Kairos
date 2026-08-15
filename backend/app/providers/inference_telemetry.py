"""Strictly allowlisted structured events for inference and plan lifecycles."""

from __future__ import annotations

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
    unknown = fields.keys() - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"telemetry fields are not allowlisted: {sorted(unknown)}")
    safe_fields = {name: _normalize_field(name, value) for name, value in fields.items()}
    _LOGGER.info(event_name, extra={"event_name": event_name, **safe_fields})
