"""SSE 游标解析共享工具（task SSE 与 execution timeline stream 共用）。"""
from __future__ import annotations

from fastapi import HTTPException, Request

MAX_EVENT_ID = 2**63 - 1


def parse_event_id(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 < len(value) <= 19:
        raise HTTPException(status_code=400, detail="Invalid event cursor")
    cursor = int(value)
    if cursor > MAX_EVENT_ID:
        raise HTTPException(status_code=400, detail="Invalid event cursor")
    return cursor


def parse_last_event_id(request: Request, after_id: str | None) -> int:
    header = request.headers.get("last-event-id")
    if header is not None:
        return parse_event_id(header)
    if after_id is not None:
        return parse_event_id(after_id)
    return 0
