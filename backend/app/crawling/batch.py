"""Scrapy 批量 Fetch 执行器（M-10 / D-009 TIER1 / 七 / 二十五 / 二十六）。

Scrapy 只是“大量已允许静态 URL 的 batch fetch executor”，与普通 HTTP 共享
AccessDecision / robots / SSRF / Credential policy / 错误分类；不改变访问权限
（403/login/captcha/robots 不能被 Scrapy 绕过）。为避免第二套 HTTP client（十六），
复用 SafeFetchHttp + FetchNodeExecutor.process_row 同一安全路径，仅加有界并发调度。
Result 统一转换成同一个 FetchResult / PageSnapshot / error taxonomy。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.crawling.contracts import FetchResult
from app.crawling.fetch_executor import FetchNodeExecutor
from app.discovery.robots import DEFAULT_USER_AGENT, RobotsCache
from app.domain.models import Run, URLResource
from app.domain.repository import SpecVersionRepository
from app.infra.object_storage import ObjectStorage


@dataclass
class BatchFetchResult:
    results: list[FetchResult] = field(default_factory=list)

    @property
    def fetched(self) -> int:
        return sum(1 for r in self.results if r.status == "SUCCESS")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "FAILED")

    @property
    def browser_pending(self) -> int:
        return sum(1 for r in self.results if r.status == "BROWSER_PENDING")


class ScrapyBatchFetcher:
    """有界并发的静态层批量执行器（Scrapy 语义）。"""

    def __init__(
        self,
        db: Any,
        *,
        executor: FetchNodeExecutor | None = None,
        http=None,
        robots: RobotsCache | None = None,
        storage: ObjectStorage | None = None,
        site_strategy=None,
        credential_resolver=None,
        user_agent: str = DEFAULT_USER_AGENT,
        allow_hosts: frozenset[str] = frozenset(),
        max_concurrency: int = 4,
        retry_base_seconds: float = 1.0,
    ) -> None:
        self._db = db
        self._executor = executor or FetchNodeExecutor(
            db,
            http=http,
            robots=robots,
            storage=storage,
            site_strategy=site_strategy,
            credential_resolver=credential_resolver,
            user_agent=user_agent,
            allow_hosts=allow_hosts,
            retry_base_seconds=retry_base_seconds,
        )
        self._max_concurrency = max_concurrency

    async def run(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
        urls: list[URLResource],
    ) -> BatchFetchResult:
        """对一批已允许静态 URL 做有界并发抓取；单 URL 失败不毒化同批。"""
        run = self._db.get(Run, run_id)
        if run is None:
            return BatchFetchResult()
        spec = SpecVersionRepository(self._db).get_version(
            run.user_id, run.task_id, run.spec_version
        )
        robots = self._executor.robots_cache()
        sem = asyncio.Semaphore(self._max_concurrency)

        async def _process(row: URLResource) -> FetchResult:
            async with sem:
                return await self._executor.process_row(run, spec, row, robots)

        results = await asyncio.gather(*(_process(r) for r in urls))
        return BatchFetchResult(results=list(results))

    @staticmethod
    def ready_urls(db: Any, *, user_id: int, task_id: int, limit: int = 500) -> list[URLResource]:
        """取当前 READY_FOR_FETCH 静态 URL（Scrapy 只消费允许清单）。"""
        from app.discovery.frontier import UrlFrontierRepository

        return UrlFrontierRepository(db).list_ready_for_fetch(
            user_id=user_id, task_id=task_id, limit=limit
        )
