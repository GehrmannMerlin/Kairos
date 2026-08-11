"""M-09 discovery domain models: 来源枚举、Frontier 状态、发现证据、候选站点。

同一 task/spec/run + canonical URL 只形成一个有效 Frontier Identity（D-016）；
重复发现累加 discovery_count 与证据，不重复执行相同 URL。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DiscoverySource(StrEnum):
    """候选 URL 为什么进入 Frontier（D-068 审计：URL 从哪里来）。"""

    USER_SEED = "USER_SEED"
    SEARCH_RESULT = "SEARCH_RESULT"
    SITEMAP = "SITEMAP"
    SITEMAP_INDEX = "SITEMAP_INDEX"
    RSS = "RSS"
    ATOM = "ATOM"
    NAVIGATION = "NAVIGATION"
    PAGINATION = "PAGINATION"
    INTERNAL_LINK = "INTERNAL_LINK"
    ROBOTS_SITEMAP = "ROBOTS_SITEMAP"


class FrontierState(StrEnum):
    """统一 canonical 状态词汇（禁止 QUEUED_URL/READY_URL/PENDING_FETCH_URL 并存）。"""

    DISCOVERED = "DISCOVERED"
    ACCESS_ALLOWED = "ACCESS_ALLOWED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    READY_FOR_FETCH = "READY_FOR_FETCH"
    HANDED_OFF = "HANDED_OFF"


class SearchResultRef(BaseModel):
    """统一的 canonical SearchResult（D-069：不把 Provider 原始 JSON 传播系统）。"""

    url: str
    title: str = ""
    snippet: str = ""
    provider: str = ""
    rank: int | None = None
    query: str = ""


class DiscoveryEvidence(BaseModel):
    """每个候选来源至少知道：来源类型 + 出处（query/provider/rank/result_url/parent）。"""

    source: DiscoverySource
    query: str | None = None
    provider: str | None = None
    rank: int | None = None
    result_url: str | None = None
    parent_url_hash: str | None = None
    note: str | None = None


class CandidateSite(BaseModel):
    """同一站点多条发现的合并站点候选（保留原始证据，不丢失来源）。"""

    site_host: str
    display_url: str
    evidence: list[SearchResultRef] = []
    depth: int = 0


_PRIORITY_BY_SOURCE = {
    DiscoverySource.USER_SEED: 100,
    DiscoverySource.ROBOTS_SITEMAP: 80,
    DiscoverySource.SITEMAP: 75,
    DiscoverySource.SITEMAP_INDEX: 75,
    DiscoverySource.SEARCH_RESULT: 60,
    DiscoverySource.RSS: 50,
    DiscoverySource.ATOM: 50,
    DiscoverySource.NAVIGATION: 30,
    DiscoverySource.PAGINATION: 25,
    DiscoverySource.INTERNAL_LINK: 10,
}


def priority_for(source: DiscoverySource, *, rank: int | None = None) -> int:
    """确定性优先级（不用 LLM score 唯一排序，规则可测试）。"""
    base = _PRIORITY_BY_SOURCE.get(source, 0)
    if source == DiscoverySource.SEARCH_RESULT and rank is not None:
        base += max(0, 10 - rank)  # rank 越小越靠前，得分越高
    return base
