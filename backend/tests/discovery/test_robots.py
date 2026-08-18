"""M-09 Task 3: robots.txt parse/policy — allow, deny, sitemap, default respect."""

from __future__ import annotations

import pytest
from app.discovery.http import DiscoveryHttp
from app.discovery.robots import RobotsPolicy, fetch_robots, parse_robots


def test_parse_allow_deny_and_sitemap() -> None:
    policy = parse_robots(
        "User-agent: *\n"
        "Disallow: /private/\n"
        "Allow: /public/\n"
        "Disallow: /no-all\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    assert policy.allowed("https://example.com/public/x")
    assert not policy.allowed("https://example.com/private/x")
    assert not policy.allowed("https://example.com/no-all")
    assert policy.allowed("https://example.com/elsewhere")
    assert policy.sitemap_urls() == ["https://example.com/sitemap.xml"]


def test_no_robots_means_allow_all() -> None:
    policy = parse_robots("")
    assert policy.allowed("https://example.com/anything")


def test_specific_user_agent_wins_over_asterisk() -> None:
    policy = parse_robots("User-agent: *\nDisallow: /\nUser-agent: KairosBot\nAllow: /ok\n")
    assert policy.allowed("https://example.com/ok", user_agent="KairosBot")
    assert not policy.allowed("https://example.com/other", user_agent="KairosBot")


def test_longest_match_wins() -> None:
    policy = parse_robots("User-agent: *\nAllow: /a\nDisallow: /a/b\n")
    assert policy.allowed("https://example.com/a/c")
    assert not policy.allowed("https://example.com/a/b/c")


class _RaisingTransport:
    async def request(self, *, method: str, url: str, timeout_seconds: float):
        raise RuntimeError("connection failed")


@pytest.mark.asyncio
async def test_fetch_robots_transport_error_degrades_to_allow() -> None:
    """robots.txt 网络失败不得让 AccessRulesCheck 节点崩溃，按「无规则 → 允许」处理。"""
    http = DiscoveryHttp(
        transport=_RaisingTransport(), allow_hosts=frozenset({"robots.example.test"})
    )
    policy = await fetch_robots(http, "http://robots.example.test/robots.txt")
    assert isinstance(policy, RobotsPolicy)
    assert policy.allowed("http://robots.example.test/anything")
