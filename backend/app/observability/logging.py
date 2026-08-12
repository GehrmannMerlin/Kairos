"""统一结构化日志：注入 trace_id/task_id/run_id 上下文并逐行脱敏。

- ``configure_logging(service)``：幂等地给 root logger 挂上脱敏 + 上下文 Filter。
- OTel 可用时，当前 span 的 trace_id 自动写入日志记录（与 API/Worker 链路关联）。
"""

from __future__ import annotations

import logging
from contextlib import suppress

from app.observability.context import get_log_context
from app.observability.redaction import redact_line

_CONTEXT_FIELDS = ("trace_id", "task_id", "run_id", "node_run_id")


class _ScrubFilter(logging.Filter):
    """注入业务上下文字段，并对消息逐行脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        ctx = get_log_context()
        for field in _CONTEXT_FIELDS:
            value = ctx.get(field) or getattr(record, field, None)
            if value is not None:
                setattr(record, field, value)
        with suppress(Exception):  # 脱敏绝不能让日志写出失败
            record.msg = redact_line(str(record.msg))
        if record.args:
            record.args = tuple(
                redact_line(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


class _OtelTraceFilter(logging.Filter):
    """把当前 OTel span 的 trace_id 写到日志记录（无 span 时静默跳过）。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        with suppress(Exception):  # 无 OTel 时降级为普通日志
            from opentelemetry import trace

            span = trace.get_current_span()
            span_context = span.get_span_context() if span.is_recording() else None
            if span_context is not None and span_context.is_valid:
                record.trace_id = f"{span_context.trace_id:032x}"  # type: ignore[attr-defined]
        return True


_configured = False


def configure_logging(service: str = "kairos") -> None:
    """幂等地给 root logger 挂上脱敏 + 上下文 + OTel trace Filter。"""
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.addFilter(_ScrubFilter())
    root.addFilter(_OtelTraceFilter())
    # 结构字段通过 LogRecord 额外属性暴露（JsonFormatter 可直接引用）。
    _configured = True
