"""Persistent URL Frontier（M-09 / D-016 / D-068）。

canonical 去重由 DB 唯一约束（task_id + url_hash）兜底；重复发现只累加
discovery_count 与 evidence，不创建第二个有效 Frontier Entry。每个批次业务数据
提交成功即形成 checkpoint（DB 事务提交），Worker 重试按幂等 upsert 续跑。
"""

from __future__ import annotations

from app.discovery.errors import DiscoveryError
from app.discovery.models import DiscoveryEvidence, DiscoverySource, FrontierState, priority_for
from app.discovery.url import canonicalize_and_hash
from app.domain.models import URLResource


class UrlFrontierRepository:
    def __init__(self, db) -> None:
        self._db = db

    def upsert_discovery(
        self,
        *,
        task_id: int,
        user_id: int,
        run_id: int,
        spec_version: int,
        raw_url: str,
        source: DiscoverySource,
        evidence: DiscoveryEvidence | None = None,
        depth: int = 0,
        priority: int | None = None,
    ) -> tuple[str, bool]:
        canonical, url_hash = canonicalize_and_hash(raw_url)
        row = (
            self._db.query(URLResource)
            .filter(URLResource.task_id == task_id, URLResource.url_hash == url_hash)
            .first()
        )
        if row is not None:
            row.discovery_count = (row.discovery_count or 1) + 1
            row.discovery_evidence = (evidence or DiscoveryEvidence(source=source)).model_dump(
                mode="json"
            )
            self._db.add(row)
            self._db.commit()
            return url_hash, False
        row = URLResource(
            task_id=task_id,
            user_id=user_id,
            run_id=run_id,
            url=canonical,
            url_hash=url_hash,
            source_type=source.value,
            status=FrontierState.DISCOVERED.value,
            spec_version=spec_version,
            discovery_source=source.value,
            discovery_count=1,
            discovery_evidence=(evidence or DiscoveryEvidence(source=source)).model_dump(
                mode="json"
            ),
            depth=depth,
            priority=priority if priority is not None else priority_for(source),
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return url_hash, True

    def _owned(self, user_id: int, url_hash: str) -> URLResource:
        row = (
            self._db.query(URLResource)
            .filter(URLResource.user_id == user_id, URLResource.url_hash == url_hash)
            .first()
        )
        if row is None:
            raise DiscoveryError("URL 不属于当前用户或不存在")
        return row

    def mark_state(self, *, user_id: int, url_hash: str, state: FrontierState) -> URLResource:
        row = self._owned(user_id, url_hash)
        row.status = state.value
        self._db.add(row)
        self._db.commit()
        return row

    def mark_blocked(self, *, user_id: int, url_hash: str, reason: str) -> None:
        row = self._owned(user_id, url_hash)
        row.status = FrontierState.BLOCKED.value
        evidence = dict(row.discovery_evidence or {})
        evidence["note"] = reason
        row.discovery_evidence = evidence
        self._db.add(row)
        self._db.commit()

    def increment_discovery_count(self, *, user_id: int, url_hash: str) -> int:
        row = self._owned(user_id, url_hash)
        row.discovery_count = (row.discovery_count or 1) + 1
        self._db.add(row)
        self._db.commit()
        return row.discovery_count

    def list_by_state(
        self, *, user_id: int, task_id: int, state: FrontierState
    ) -> list[URLResource]:
        return (
            self._db.query(URLResource)
            .filter(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.status == state.value,
            )
            .order_by(URLResource.id.asc())
            .all()
        )

    def list_ready_for_fetch(
        self, *, user_id: int, task_id: int, limit: int = 200
    ) -> list[URLResource]:
        """M-10 Handoff：只消费 READY_FOR_FETCH，按 priority 降序。"""
        return (
            self._db.query(URLResource)
            .filter(
                URLResource.user_id == user_id,
                URLResource.task_id == task_id,
                URLResource.status == FrontierState.READY_FOR_FETCH.value,
            )
            .order_by(URLResource.priority.desc(), URLResource.id.asc())
            .limit(limit)
            .all()
        )
