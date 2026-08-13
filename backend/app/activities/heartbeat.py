"""Activity heartbeat helper（M-07）。

heartbeat 只用于存活/进度/取消响应，绝不生成业务 Checkpoint（D-015/D-030）。
details 只放安全、最小、非 Secret 的执行进度。
"""

from __future__ import annotations

from temporalio import activity


def heartbeat_progress(*, done: int, total: int | None = None, note: str = "") -> None:
    details = {"done": done, "note": note}
    if total is not None:
        details["total"] = total
    activity.heartbeat(details)
