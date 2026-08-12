"""结构化日志上下文（contextvars）——让日志与 OTel trace / 任务关联。

日志格式推荐字段见 agent-code-standards.md §10.2（trace_id/user_id/task_id/
run_id/node_run_id/provider/error_class/attempt/duration_ms）。
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_log_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "kairos_log_context", default=None
)


def get_log_context() -> dict[str, Any]:
    return dict(_log_context.get() or {})


@contextmanager
def bind_log_context(**kw: Any) -> Iterator[None]:
    """``with bind_log_context(task_id=..., run_id=...): ...`` 期间日志自动携带这些字段。"""
    token = _log_context.set({**(get_log_context()), **kw})
    try:
        yield
    finally:
        _log_context.reset(token)
