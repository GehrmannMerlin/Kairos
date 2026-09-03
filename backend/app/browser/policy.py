"""Deterministic Browser egress policy and read-only action denylist.

Phase 4 first version keeps the browser read-only: it never logs in, submits,
purchases, uploads, downloads, or mutates remote state. The egress allowlist is
built from the source hostname plus the collection scope domains; private
addresses are always blocked. This module is pure/domain-facing (no Playwright
import at module scope except the type factories).
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic_ai_harness.playwright import EgressPolicy

from app.domain import BrowserLimits, CollectionError
from app.url_policy import validate_public_http_url

# Deterministic denylist for click targets / action names that would mutate
# remote state or perform a risky flow. Browser instructions plus this list are
# the Phase 4 read-only guard; there is no large classifier. It includes
# mutating verbs (type/fill/press/submit/send/...) alongside the risky flows.
HIGH_RISK_ACTION_NAMES: frozenset[str] = frozenset(
    {
        "login",
        "sign in",
        "signin",
        "register",
        "sign up",
        "buy",
        "purchase",
        "checkout",
        "submit",
        "send",
        "post",
        "delete",
        "remove",
        "save",
        "confirm",
        "subscribe",
        "download",
        "upload",
        "add to cart",
        "pay",
        "publish",
        "create account",
        "type",
        "fill",
        "password",
        "textarea",
        "form",
    }
)

# Mutating Playwright tool names that the temporary Browser Agent must never hold.
FORBIDDEN_BROWSER_TOOLS: frozenset[str] = frozenset(
    {
        "type_text",
        "press_key",
        "select_option",
        "handle_next_dialog",
        "execute_js",
        "go_back",
        "go_forward",
        "tabs",
        "network_requests",
        "console_messages",
    }
)
# Read-only subset the Phase 4 Browser Agent is allowed to use.
ALLOWED_BROWSER_TOOLS: frozenset[str] = frozenset(
    {"navigate", "snapshot", "get_text", "scroll", "wait_for", "click", "hover", "screenshot"}
)

POLICY_VERSION = "browser-policy-v1"


def action_is_read_only(name: str) -> bool:
    """Return False for any action text containing a high-risk workflow keyword."""
    normalized = " ".join(name.strip().lower().split())
    if not normalized:
        return False
    return not any(risk in normalized for risk in HIGH_RISK_ACTION_NAMES)


def _host_of(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def resolve_browser_domains(
    source_url: str,
    scope_domains: list[str],
    explicit_extra: list[str] | None = None,
) -> list[str]:
    """Return the host allowlist: source hostname + verified scope + explicit extra.

    Hostname boundary semantics: `example.com` allows `example.com` and
    `docs.example.com`, never `example.com.attacker.com`. `explicit_extra` are
    operator-granted extra hosts and are always included when they parse as a
    bare hostname. Scope domains are only honored when they are an ancestor of
    the source host (or the host itself); an unrelated scope entry cannot widen
    the browser to another site.
    """
    host = _host_of(source_url)
    if not host:
        return []
    results = [host]
    for raw in scope_domains or []:
        candidate = _bare_host(raw)
        if not candidate:
            continue
        # Ancestor-of-source scope: quotes.toscrape.com under toscrape.com.
        if candidate == host or host.endswith(f".{candidate}"):
            results.append(candidate)
    for raw in explicit_extra or []:
        candidate = _bare_host(raw)
        if candidate:
            results.append(candidate)
    return list(dict.fromkeys(results))


def _bare_host(raw: str) -> str | None:
    """Return a normalized bare hostname, or None if `raw` is not a hostname."""
    candidate = raw.strip().lower().removeprefix("*.").rstrip(".")
    if not candidate or "/" in candidate or ":" in candidate or " " in candidate:
        return None
    return candidate


def allowed_domains_for(
    source_url: str,
    scope_domains: list[str],
    explicit_extra: list[str] | None = None,
) -> list[str]:
    """Public alias: resolved domain list for the browser session egress."""
    return resolve_browser_domains(source_url, scope_domains, explicit_extra)


def egress_policy_for(
    source_url: str,
    scope_domains: list[str],
    explicit_extra: list[str] | None = None,
) -> EgressPolicy:
    """Build the Harness egress policy: allowlist the source/scope, block private, no download.

    allowlist_reach bounds navigation and data (fetch/XHR/WebSocket/sendBeacon)
    so a permitted page keeps its assets but cannot read from or post to other
    hosts; passive subresources stay unblocked so the page renders.
    """
    domains = allowed_domains_for(source_url, scope_domains, explicit_extra)
    return EgressPolicy(
        allowed_domains=domains or None,
        block_private_addresses=True,
        include_subdomains=True,
        allowlist_reach=frozenset({"navigation", "data"}),
    )


def classify_url(url: str) -> str:
    """Return 'ok' for a public http(s) URL, or 'private' / 'non_http' / 'unknown'."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "non_http"
        validate_public_http_url(url)
        return "ok"
    except CollectionError as exc:
        if exc.code == "PRIVATE_ADDRESS_BLOCKED":
            return "private"
        return "unknown"
    except ValueError:
        return "non_http"


def server_browser_limits(config: dict | None) -> BrowserLimits:
    """Clamp a raw server config dict to the hard BrowserLimits caps, never raising.

    `BrowserLimits` itself already enforces ge/le at construction; this helper
    clamps a possibly-out-of-range raw config BEFORE construction so a changed
    server setting cannot crash the claim or move the mid-run budget. The frozen
    spec value (BrowserLimits stored at confirm time) is the operative budget.
    """
    source = config or {}
    values: dict[str, int] = {}
    for field, default, floor, ceil in (
        ("max_browser_tasks_per_run", 5, 1, 10),
        ("max_steps_per_task", 20, 1, 50),
        ("task_timeout_seconds", 120, 30, 300),
        ("max_navigation_count", 5, 1, 20),
        ("max_action_events", 30, 5, 100),
    ):
        try:
            value = int(source.get(field, default))
        except (TypeError, ValueError):
            value = default
        values[field] = min(max(value, floor), ceil)
    return BrowserLimits(**values)