"""Goal Understanding attempt idempotency repository (M-06 request-lifecycle fix).

身份 = (task_id, source_message_id, input_fingerprint)。partial unique index
(status='running') 是跨 API 进程的并发兜底：同一输入同时两个 /understand，
只有一个 attempt 能进入 running，另一个返回 IN_PROGRESS。

Stale-RUNNING 保护：进程崩溃可能遗留 running 行；超过 TTL 视为死亡并接管，
避免该 task+消息被一个永远不结束的 attempt 永久阻塞。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.models import UnderstandingAttempt

# 后端 Provider 有界超时（45s）+ 写库/响应余量；超过即认为 attempt 已死。
STALE_RUNNING_TTL_SECONDS = 300


def _now() -> datetime:
    return datetime.now(UTC)


class UnderstandingAttemptRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def find_identity(
        self,
        *,
        user_id: int,
        task_id: int,
        source_message_id: int,
        input_fingerprint: str,
        status: str | None = None,
    ) -> list[UnderstandingAttempt]:
        stmt = select(UnderstandingAttempt).where(
            UnderstandingAttempt.user_id == user_id,
            UnderstandingAttempt.task_id == task_id,
            UnderstandingAttempt.source_message_id == source_message_id,
            UnderstandingAttempt.input_fingerprint == input_fingerprint,
        )
        if status is not None:
            stmt = stmt.where(UnderstandingAttempt.status == status)
        stmt = stmt.order_by(UnderstandingAttempt.id.desc())
        return list(self._db.scalars(stmt))

    def find_running(
        self,
        *,
        user_id: int,
        task_id: int,
        source_message_id: int,
        input_fingerprint: str,
    ) -> UnderstandingAttempt | None:
        rows = self.find_identity(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
            status="running",
        )
        return rows[0] if rows else None

    def find_latest_succeeded(
        self,
        *,
        user_id: int,
        task_id: int,
        source_message_id: int,
        input_fingerprint: str,
    ) -> UnderstandingAttempt | None:
        rows = self.find_identity(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
            status="succeeded",
        )
        return rows[0] if rows else None

    def find_latest_failed(
        self,
        *,
        user_id: int,
        task_id: int,
        source_message_id: int,
        input_fingerprint: str,
    ) -> UnderstandingAttempt | None:
        rows = self.find_identity(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
            status="failed",
        )
        return rows[0] if rows else None

    def begin_running(
        self,
        *,
        user_id: int,
        task_id: int,
        source_message_id: int,
        input_fingerprint: str,
        trigger_source: str,
        request_id: str | None,
        model_config_id: str | None,
        model_config_version: int | None,
        provider: str | None,
        model: str | None,
    ) -> tuple[UnderstandingAttempt | None, bool]:
        """尝试创建 running attempt。

        Returns (attempt, is_reused_running)：
        - (attempt, False) 本请求成为 in-flight owner。
        - (None, True)  有另一个 fresh running attempt（并发）→ IN_PROGRESS。
        - 若 running 已 stale（超过 TTL），先接管（标记失败）再新建。
        """
        fresh = self.find_running(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
        )
        if fresh is not None:
            if _now() - fresh.started_at < timedelta(seconds=STALE_RUNNING_TTL_SECONDS):
                return None, True
            # stale takeover：把死掉的 running 收尾，再允许新建。
            fresh.status = "failed"
            fresh.error_code = "STALE_TAKEOVER"
            fresh.finished_at = _now()
            self._db.add(fresh)
            self._db.flush()

        attempt = UnderstandingAttempt(
            user_id=user_id,
            task_id=task_id,
            source_message_id=source_message_id,
            input_fingerprint=input_fingerprint,
            status="running",
            trigger_source=trigger_source,
            request_id=request_id,
            model_config_id=model_config_id,
            model_config_version=model_config_version,
            provider=provider,
            model=model,
        )
        self._db.add(attempt)
        try:
            self._db.commit()
        except IntegrityError:
            # 并发窗口内另一个进程已插入 running → 让位。
            self._db.rollback()
            return None, True
        self._db.refresh(attempt)
        return attempt, False

    def mark_succeeded(
        self,
        attempt: UnderstandingAttempt,
        *,
        duration_ms: int,
        result_payload: dict,
        spec_draft_payload: dict,
        message_id: int,
    ) -> None:
        attempt.status = "succeeded"
        attempt.duration_ms = duration_ms
        attempt.result_payload = result_payload
        attempt.spec_draft_payload = spec_draft_payload
        attempt.result_ref_message_id = message_id
        attempt.finished_at = _now()
        self._db.add(attempt)
        self._db.commit()

    def mark_failed(
        self,
        attempt: UnderstandingAttempt,
        *,
        error_code: str,
        duration_ms: int | None = None,
    ) -> None:
        attempt.status = "failed"
        attempt.error_code = error_code
        attempt.duration_ms = duration_ms
        attempt.finished_at = _now()
        self._db.add(attempt)
        self._db.commit()
