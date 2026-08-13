"""TEST B：redaction 用 fake canary 验证日志/trace/备份均不出现明文（M-17）。

不使用真实 API key；统一用 M17_SECRET_CANARY 标记。
"""

from __future__ import annotations

import logging

from app.observability.context import bind_log_context
from app.observability.logging import _ScrubFilter
from app.observability.redaction import redact_headers, redact_line

CANARY = "M17_SECRET_CANARY_9f3a7c"


def test_redact_line_masks_canary_in_common_shapes() -> None:
    cases = [
        f"api_key={CANARY}",
        f"Authorization: Bearer {CANARY}",
        f"password={CANARY}",
        f"KAIROS_CREDENTIAL_MASTER_KEY={CANARY}",
        f"session_secret={CANARY}",
        f"postgresql+psycopg://kairos:{CANARY}@pg:5432/kairos",
        f"Cookie: kairos_session={CANARY}",
    ]
    for case in cases:
        assert CANARY not in redact_line(case), case
        assert "<redacted" in redact_line(case), case


def test_redact_headers_strips_values() -> None:
    out = redact_headers({"Authorization": "Bearer " + CANARY, "Cookie": CANARY})
    assert CANARY not in " ".join(out.values())
    assert all(v == "<redacted>" for v in out.values())


def test_scrub_filter_scrubs_and_injects_context() -> None:
    filter_ = _ScrubFilter()
    with bind_log_context(task_id="task-77", run_id="run-9"):
        record = logging.LogRecord(
            "t", logging.INFO, "m.py", 1, f"api_key={CANARY}", (), None
        )
        assert filter_.filter(record) is True
        assert CANARY not in record.getMessage()
        assert "<redacted:api_key>" in record.getMessage()
        assert record.task_id == "task-77"
        assert record.run_id == "run-9"


def test_scrub_filter_also_scrubs_args() -> None:
    filter_ = _ScrubFilter()
    record = logging.LogRecord("t", logging.INFO, "m.py", 1, "key=%s", (f"api_key={CANARY}",), None)
    assert filter_.filter(record) is True
    assert CANARY not in record.getMessage()
    assert "<redacted:api_key>" in record.getMessage()
