"""robots.txt 获取 / 解析 / 缓存 / 决策（M-09 / D-070）。

默认遵守 robots.txt：解析 User-agent 组的 Allow/Disallow 与 Sitemap 指令，
最长匹配 + 同长 Allow 优先（RFC 9309 精神）；按 host/origin 缓存 TTL，避免
逐 URL 下载。404 / 非 200 视为“无规则”（保守允许，由上层 AccessRules 决策）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from app.discovery.http import DEFAULT_USER_AGENT, DiscoveryHttp

_MAX_ROBOTS_BYTES = 512 * 1024


@dataclass(frozen=True)
class _Rule:
    path: str
    allow: bool


@dataclass
class RobotsPolicy:
    """robots.txt 规则集：默认遵守；无规则 → 允许。"""

    rules: list[tuple[str, list[_Rule]]] = field(default_factory=list)  # (user_agent, rules)
    sitemaps: list[str] = field(default_factory=list)

    def allowed(self, url: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
        from urllib.parse import urlsplit

        path = urlsplit(url).path or "/"
        rules = self._rules_for(user_agent)
        if not rules:
            return True
        best: _Rule | None = None
        for r in rules:
            if not path.startswith(r.path):
                continue
            if (
                best is None
                or len(r.path) > len(best.path)
                or (len(r.path) == len(best.path) and r.allow and not best.allow)
            ):
                best = r
        return best is None or best.allow

    def _rules_for(self, user_agent: str) -> list[_Rule]:
        """具体 UA 组规则 + `*` 组兜底规则的并集（RFC 9309 精神）。

        具体组负责其声明的路径；`*` 组兜底其余路径（如 Disallow: / 全局禁用）。
        判定由 allowed() 的最长匹配 + Allow 优先完成。
        """
        wildcard: list[_Rule] = []
        specific: list[_Rule] = []
        for ua, rules in self.rules:
            if ua == "*":
                wildcard = rules
            elif ua and ua.lower() in user_agent.lower():
                specific = rules
        return specific + wildcard

    def sitemap_urls(self) -> list[str]:
        return list(self.sitemaps)


def parse_robots(text: str) -> RobotsPolicy:
    groups: dict[str, list[_Rule]] = {}
    sitemaps: list[str] = []
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            current = value
            groups.setdefault(current, [])
        elif key == "sitemap":
            sitemaps.append(value)
        elif key in ("allow", "disallow") and current is not None:
            if value == "":
                continue
            groups[current].append(_Rule(path=value, allow=(key == "allow")))
    return RobotsPolicy(rules=list(groups.items()), sitemaps=sitemaps)


def _robots_url_for(origin: str) -> str:
    """origin 形如 http://host:port（含端口）；robots.txt 必须请求同一 origin。"""
    return f"{origin.rstrip('/')}/robots.txt"


async def fetch_robots(http: DiscoveryHttp, robots_url: str) -> RobotsPolicy:
    resp = await http.get_text(robots_url, timeout_seconds=15.0)
    if resp.status_code not in (200, 404):
        # 非 404 错误按保守“无规则”处理（由上层 AccessRules 决定）
        return RobotsPolicy()
    if resp.status_code == 404:
        return RobotsPolicy()
    return parse_robots(resp.text[:_MAX_ROBOTS_BYTES])


@dataclass
class RobotsCache:
    """按 site origin 缓存，TTL 内不重复下载（避免逐 URL 请求 robots.txt）。

    origin 形如 http://host:port（从 seed URL 推导，保证与目标站点同 scheme/端口）。
    """

    http: DiscoveryHttp
    ttl_seconds: int = 3600
    user_agent: str = DEFAULT_USER_AGENT
    _cache: dict[str, tuple[float, RobotsPolicy]] = field(default_factory=dict)

    async def get(self, seed_url: str) -> RobotsPolicy:
        from urllib.parse import urlsplit

        parsed = urlsplit(seed_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        now = monotonic()
        cached = self._cache.get(origin)
        if cached and now - cached[0] < self.ttl_seconds:
            return cached[1]
        policy = await fetch_robots(self.http, _robots_url_for(origin))
        self._cache[origin] = (now, policy)
        return policy
