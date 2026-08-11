"""M-09 Task 6: AccessRulesCheck — allow/deny/scope/scheme + robots override boundary."""

from __future__ import annotations

from app.discovery.access_rules import AccessDecision, decide_access
from app.discovery.robots import RobotsPolicy, parse_robots


def _spec() -> dict:
    return {
        "source_scope": {
            "mode": "SPECIFIED_SOURCE",
            "seed_urls": ["https://example.com"],
            "source_hints": [],
        }
    }


def test_allow_public_robots_ok() -> None:
    policy = RobotsPolicy()  # 无规则 → allow
    assert (
        decide_access("https://example.com/x", spec=_spec(), robots_policy=policy)
        == AccessDecision.ALLOW
    )


def test_robots_denied_public_is_overrideable() -> None:
    policy = parse_robots("User-agent: *\nDisallow: /private/\n")
    assert (
        decide_access("https://example.com/private/x", spec=_spec(), robots_policy=policy)
        == AccessDecision.ROBOTS_DENIED_PUBLIC
    )


def test_scope_out_and_scheme_invalid() -> None:
    assert (
        decide_access("https://other.com/x", spec=_spec(), robots_policy=RobotsPolicy())
        == AccessDecision.SCOPE_OUT
    )
    assert (
        decide_access("ftp://example.com/x", spec=_spec(), robots_policy=RobotsPolicy())
        == AccessDecision.SCHEME_INVALID
    )
