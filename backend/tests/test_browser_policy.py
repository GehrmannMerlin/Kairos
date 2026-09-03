"""Phase 4 browser egress / read-only policy tests."""

from __future__ import annotations

from app.browser.policy import (
    HIGH_RISK_ACTION_NAMES,
    action_is_read_only,
    allowed_domains_for,
    egress_policy_for,
    resolve_browser_domains,
    server_browser_limits,
)
from pydantic_ai_harness.playwright import EgressPolicy


def test_high_risk_action_names_denylist() -> None:
    risky_actions = (
        "login",
        "sign in",
        "register",
        "buy",
        "purchase",
        "checkout",
        "submit",
        "delete",
        "upload",
        "download",
    )
    for risky in risky_actions:
        assert risky in HIGH_RISK_ACTION_NAMES
        assert not action_is_read_only(risky)


def test_read_only_actions_allowed() -> None:
    for safe in ("Load More", "Expand", "Click a tab", "Scroll", "Close cookie banner"):
        assert action_is_read_only(safe)


def test_explicit_type_like_actions_rejected() -> None:
    assert not action_is_read_only("Type into field")
    assert not action_is_read_only("Fill password")
    assert not action_is_read_only("Press Enter to submit")


def test_allowed_domains_for_source() -> None:
    domains = allowed_domains_for(
        source_url="https://quotes.toscrape.com/js/",
        scope_domains=[],
        explicit_extra=None,
    )
    assert domains == ["quotes.toscrape.com"]
    assert "example.com" not in domains


def test_allowed_domains_scope_expansion() -> None:
    domains = allowed_domains_for(
        source_url="https://quotes.toscrape.com/js/",
        scope_domains=["toscrape.com"],
        explicit_extra=["docs.example.com"],
    )
    assert "quotes.toscrape.com" in domains
    assert "toscrape.com" in domains
    # explicit_extra is operator-granted and included verbatim.
    assert "docs.example.com" in domains
    assert all("attacker" not in d for d in domains)


def test_resolve_browser_domains_rejects_evil_suffix() -> None:
    domains = resolve_browser_domains(
        "https://example.com.attacker.com/",
        scope_domains=["example.com"],
        explicit_extra=[],
    )
    # The attacker-owned host is its own source host here; what must NOT appear
    # is the bare `example.com` scope entry, because a `contains("example.com")`
    # style match would wrongly widen to the real example.com.
    assert "example.com" not in domains
    assert len(domains) == 1
    assert domains[0] == "example.com.attacker.com"


def test_egress_policy_private_blocked_and_allowlist() -> None:
    policy = egress_policy_for(
        source_url="https://quotes.toscrape.com/js/",
        scope_domains=[],
        explicit_extra=None,
    )
    assert isinstance(policy, EgressPolicy)
    assert policy.block_private_addresses is True
    assert "quotes.toscrape.com" in (policy.allowed_domains or [])
    assert not any("*" in d for d in (policy.allowed_domains or []))


def test_server_browser_limits_clamps_hard_max() -> None:
    configured = {
        "max_browser_tasks_per_run": 99,
        "max_steps_per_task": 999,
        "task_timeout_seconds": 99999,
        "max_navigation_count": 99,
        "max_action_events": 999,
    }
    clamped = server_browser_limits(configured)
    assert clamped.max_browser_tasks_per_run == 10
    assert clamped.max_steps_per_task == 50
    assert clamped.task_timeout_seconds == 300
    assert clamped.max_navigation_count == 20
    assert clamped.max_action_events == 100


def test_server_browser_limits_never_raises() -> None:
    # A server config with a single below-floor field must clamp, not crash the claim.
    clamped = server_browser_limits({"max_steps_per_task": 0})
    assert clamped.max_steps_per_task >= 1
    assert clamped.max_browser_tasks_per_run == 5
    assert clamped.task_timeout_seconds == 120