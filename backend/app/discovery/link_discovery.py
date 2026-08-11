"""LinkDiscovery + site-level expansion（M-09 / D-068 第二层）。

对候选站点/ACCESS_ALLOWED URL 执行站内 URL 扩展：sitemap（含 robots Sitemap
directive + /sitemap.xml 安全 fallback）、RSS/Atom feed、HTML 导航/分页/内链。
发现的 URL 规范化后写入 Frontier（带 parent/来源/优先级）。跨域链接不作为
Frontier 直接消费项，仅作为候选提示（避免友情链接无限制扩散整个 Web）。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from app.discovery.errors import DiscoveryError
from app.discovery.url import canonicalize_and_hash

_SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class LinkDiscoveryError(DiscoveryError):
    pass


def parse_sitemap(xml_text: str) -> list[str]:
    """解析 sitemap urlset 或 sitemap index；返回 loc 列表（含子 sitemap URL）。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LinkDiscoveryError("sitemap 解析失败") from exc
    urls: list[str] = []
    if root.tag.endswith("sitemapindex"):
        for child in root.findall("s:sitemap/s:loc", _SITEMAP_NS):
            urls.append((child.text or "").strip())
    else:
        for child in root.findall("s:url/s:loc", _SITEMAP_NS):
            urls.append((child.text or "").strip())
    return [u for u in urls if u]


def parse_feed(xml_text: str) -> list[str]:
    """解析 RSS 2.0 或 Atom；返回 entry/page URL 列表。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LinkDiscoveryError("feed 解析失败") from exc
    tag = root.tag.lower()
    urls: list[str] = []
    if "rss" in tag:
        for loc in root.findall(".//link"):
            urls.append((loc.text or "").strip())
    elif "feed" in tag:  # Atom
        for link in root.findall(f".//{_ATOM_NS}link"):
            href = link.get("href") or ""
            if href:
                urls.append(href)
    return [u for u in urls if u]


_HTML_LINK_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
_ASSET_EXT = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|css|js|woff2?|ttf|ico)$", re.IGNORECASE)
_PAGINATION_RE = re.compile(r"(?:page|p)[=/](\d+)", re.IGNORECASE)
_REL_NEXT_PREV = re.compile(r"\brel\s*=\s*[\"'][^\"']*\b(?:next|prev)\b", re.IGNORECASE)


def extract_html_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """从 HTML 提取候选 <a href>，返回 (absolute_url, rel)。

    rel: "pagination"（rel=next/prev 或 page=N 模式）| "internal"（导航/普通内链）。
    排除锚点、mailto/javascript/tel 与图片/样式/脚本等资产链接。
    """
    from urllib.parse import urljoin

    links: list[tuple[str, str]] = []
    for m in _HTML_LINK_RE.finditer(html):
        href = m.group(1).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        if _ASSET_EXT.search(href):
            continue
        full = urljoin(base_url, href)
        rel = (
            "pagination"
            if (_REL_NEXT_PREV.search(href) or _PAGINATION_RE.search(href))
            else "internal"
        )
        links.append((full, rel))
    return links


class LinkDiscoveryService:
    """LinkDiscovery executor：站内扩展 + 每个发现 URL 的 robots/scope 决策。"""

    def __init__(self, db, *, http=None, robots=None, user_agent="KairosBot", max_links=200):
        from app.discovery.http import DiscoveryHttp
        from app.discovery.robots import DEFAULT_USER_AGENT, RobotsCache

        self._db = db
        self._user_agent = user_agent or DEFAULT_USER_AGENT
        self._http = http or DiscoveryHttp()
        self._robots = robots or RobotsCache(self._http)
        self._max_links = max_links

    async def _expand_seed(self, seed, run, spec):
        """扩展单个站点 seed：sitemap/feed/HTML 链接 → 新 canonical URL 候选。"""
        from urllib.parse import urlsplit

        from app.discovery.frontier import UrlFrontierRepository
        from app.discovery.models import (
            DiscoveryEvidence,
            DiscoverySource,
            FrontierState,
        )

        parsed_seed = urlsplit(seed.url)
        host = (parsed_seed.hostname or "").lower()
        origin = f"{parsed_seed.scheme}://{parsed_seed.netloc}"
        frontier = UrlFrontierRepository(self._db)
        policy = await self._robots.get(seed.url)
        discovered: list[tuple[str, DiscoverySource]] = []

        # sitemap：robots Sitemap directive + /sitemap.xml fallback（同 origin，含端口）
        sitemaps = policy.sitemap_urls() or [f"{origin}/sitemap.xml"]
        for sm_url in sitemaps[:2]:
            try:
                resp = await self._http.get_text(sm_url, timeout_seconds=15.0)
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            try:
                for u in parse_sitemap(resp.text)[: self._max_links]:
                    discovered.append((u, DiscoverySource.SITEMAP))
            except LinkDiscoveryError:
                continue

        # RSS/Atom：常见路径 fallback（同 origin）
        for feed_url in (f"{origin}/rss.xml", f"{origin}/feed.xml"):
            try:
                resp = await self._http.get_text(feed_url, timeout_seconds=10.0)
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            if "<rss" in resp.text or "<feed" in resp.text:
                try:
                    for u in parse_feed(resp.text)[: self._max_links]:
                        discovered.append((u, DiscoverySource.RSS))
                except LinkDiscoveryError:
                    continue

        # seed HTML：导航/分页/内链
        try:
            resp = await self._http.get_text(seed.url, timeout_seconds=15.0)
        except Exception:
            resp = None
        if resp is not None and resp.status_code == 200:
            for full, rel in extract_html_links(resp.text, seed.url)[: self._max_links]:
                src = (
                    DiscoverySource.PAGINATION
                    if rel == "pagination"
                    else DiscoverySource.INTERNAL_LINK
                )
                discovered.append((full, src))

        added = 0
        blocked = 0
        cross = 0
        for raw, source in discovered:
            try:
                canonical, url_hash = canonicalize_and_hash(raw)
            except DiscoveryError:
                continue
            if (urlsplit(canonical).hostname or "").lower() != host:
                cross += 1  # 跨域 → 候选提示，不入 Frontier
                continue
            if not policy.allowed(canonical, user_agent=self._user_agent):
                frontier.upsert_discovery(
                    task_id=run.task_id,
                    user_id=run.user_id,
                    run_id=run.id,
                    spec_version=run.spec_version,
                    raw_url=canonical,
                    source=source,
                    evidence=DiscoveryEvidence(
                        source=source, parent_url_hash=seed.url_hash, note="robots_denied"
                    ),
                    depth=seed.depth + 1,
                )
                frontier.mark_blocked(
                    user_id=run.user_id, url_hash=url_hash, reason="robots_denied"
                )
                blocked += 1
                continue
            frontier.upsert_discovery(
                task_id=run.task_id,
                user_id=run.user_id,
                run_id=run.id,
                spec_version=run.spec_version,
                raw_url=canonical,
                source=source,
                evidence=DiscoveryEvidence(source=source, parent_url_hash=seed.url_hash),
                depth=seed.depth + 1,
            )
            frontier.mark_state(
                user_id=run.user_id, url_hash=url_hash, state=FrontierState.READY_FOR_FETCH
            )
            added += 1
        return added, blocked, cross

    async def execute(self, unit):
        from app.activities.execution_seam import ExecuteUnitResult
        from app.discovery.frontier import UrlFrontierRepository
        from app.discovery.models import FrontierState
        from app.domain.models import Run
        from app.domain.repository import SpecVersionRepository

        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="FAILED",
                error_code="RUN_NOT_FOUND",
                committed_refs={},
            )
        spec = SpecVersionRepository(self._db).get_version(
            run.user_id, run.task_id, run.spec_version
        )
        params = unit.parameters or {}
        max_links = int(params.get("max_links") or self._max_links)
        self._max_links = max_links
        frontier = UrlFrontierRepository(self._db)
        seeds = frontier.list_by_state(
            user_id=run.user_id, task_id=run.task_id, state=FrontierState.ACCESS_ALLOWED
        )
        total_added = 0
        total_blocked = 0
        total_cross = 0
        for seed in seeds[:50]:
            added, blocked, cross = await self._expand_seed(seed, run, spec)
            # seed 本身访问已确认 → 置 READY_FOR_FETCH（站点入口也是要抓的页面）
            frontier.mark_state(
                user_id=run.user_id, url_hash=seed.url_hash, state=FrontierState.READY_FOR_FETCH
            )
            total_added += added
            total_blocked += blocked
            total_cross += cross
        from app.state.events import append_domain_event

        append_domain_event(
            self._db,
            user_id=run.user_id,
            aggregate_type="task",
            aggregate_id=run.task_id,
            event_type="discovery.expanded",
            aggregate_version=1,
            payload={
                "seeds": len(seeds),
                "added": total_added,
                "blocked": total_blocked,
                "cross_domain_hints": total_cross,
            },
            actor_type="system",
            run_id=run.id,
            node_run_id=None,
        )
        self._db.commit()
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
                "task_id": run.task_id,
                "seeds": len(seeds),
                "added": total_added,
                "blocked": total_blocked,
                "cross_domain_hints": total_cross,
            },
        )
