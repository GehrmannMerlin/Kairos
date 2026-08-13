"""站点级成功抓取策略（M-10 / D-009 策略复用 / 六十四 / 二十二）。

同站成功后写入已验证策略（preferred tier + TTL）；策略失效或结构变化 → 重新探测。
策略不能成为永久 bypass authorization：Fetch/Browser 执行器对每个 URL 仍先重新执行
AccessDecision/robots/scope，策略只决定“用什么工具”，不决定“能否访问”。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.crawling.repository import SiteFetchStrategyRepository

_MAX_CONSECUTIVE_FAILURES = 3


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite 回读的 datetime 是 naive；统一补成 UTC 以便与 aware _now() 比较。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SiteStrategyService:
    def __init__(self, db: Any, *, ttl_seconds: int = 86400, repository=None) -> None:
        self._db = db
        self._ttl_seconds = ttl_seconds
        self._repo = repository or SiteFetchStrategyRepository(db)

    def decide(self, *, user_id: int, site_host: str):
        """返回有效策略（valid 且未过期）；否则 None（重新探测）。"""
        row = self._repo.get(user_id, site_host)
        if row is None:
            return None
        if row.state != "valid":
            return None
        expires = _as_utc(row.expires_at)
        if expires is not None and expires < _now():
            return None
        return row

    def record_success(
        self,
        *,
        user_id: int,
        site_host: str,
        tier: str,
        tool: str,
        tool_version: str,
        structure_fingerprint: str | None = None,
        credential_required: bool = False,
        credential_type: str | None = None,
    ) -> None:
        self._repo.upsert(
            user_id=user_id,
            site_host=site_host,
            preferred_tier=tier,
            tool=tool,
            tool_version=tool_version,
            structure_fingerprint=structure_fingerprint,
            credential_required=credential_required,
            credential_type=credential_type,
            last_success_at=_now(),
            expires_at=_now() + timedelta(seconds=self._ttl_seconds),
            failure_count=0,
            state="valid",
        )

    def record_failure(self, *, user_id: int, site_host: str) -> None:
        """连续失败累计；达到阈值 → 策略失效（重新探测）。"""
        row = self._repo.get(user_id, site_host)
        if row is None:
            return
        failures = (row.failure_count or 0) + 1
        if failures >= _MAX_CONSECUTIVE_FAILURES:
            self._repo.invalidate(user_id, site_host)
            return
        self._repo.upsert(
            user_id=user_id,
            site_host=site_host,
            preferred_tier=row.preferred_tier,
            tool=row.tool,
            tool_version=row.tool_version,
            structure_fingerprint=row.structure_fingerprint,
            credential_required=row.credential_required,
            credential_type=row.credential_type,
            last_success_at=row.last_success_at,
            expires_at=row.expires_at,
            failure_count=failures,
            state="valid" if row.state == "valid" else row.state,
        )

    @staticmethod
    def structure_fingerprint(
        *, content_type: str | None, body_hash: str, html_markers: list[str]
    ) -> str:
        """确定性结构指纹（不依赖 LLM）；页面结构变化时指纹变化 → 重新探测。"""
        marker = "|".join(sorted(m for m in html_markers if m)) or "none"
        suffix = f":{content_type}" if content_type else ""
        return f"{body_hash[:16]}{suffix}:{marker}"
