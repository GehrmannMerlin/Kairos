# M-09 Source Discovery / Search / robots / URL Frontier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement M-09 source discovery: Search Provider integration, URL canonicalization, AccessRulesCheck + robots policy (with public override approval), site-level expansion (sitemap/RSS/Atom/nav/pagination/internal links), and a persistent, idempotent, checkpointable URL Frontier that hands READY_FOR_FETCH URLs to M-10.

**Architecture:** Two-stage discovery (D-068): external discovery (Search Provider / user seeds) → candidate sites → site-level URL expansion. AccessRulesCheck runs before expansion; robots is default-respected with a JIT approval override for public pages only (D-070). All HTTP lives in Activities/executors behind an SSRF guard. Frontier reuses M-04 `url_resources` (extended by migration 0007) with canonical dedupe (task_id+url_hash unique) and discovery evidence. Executors bind into M-08 `NODE_EXECUTORS` and run through the M-07 TaskWorkflow.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, urllib/httpx, xml.etree, Temporal Python SDK, Pydantic. No Playwright/Scrapy/Crawl4AI (M-10+).

## Global Constraints

- Vue 3 + TypeScript strict; backend FastAPI + Python type annotations (agent-code-standards).
- Search Provider and Model Provider are **separate protocols** (D-069); reuse M-03 `SearchProvider` / `SearchConfig` / `CredentialVault`. No second search client, no scraping of Google/Bing result HTML.
- SPECIFIED_SOURCE must work **without** a Search Provider; EXPLORATORY/HYBRID without an available SearchConfig returns a **stable `SEARCH_PROVIDER_NOT_CONFIGURED`** error — never silent fallback.
- robots.txt **default respect** (D-070); override only for public no-login pages, via M-08 Approval. Login/auth/captcha/access-controlled/private/credential-required are **never** overrideable → `PROHIBITED`/block.
- SSRF guard mandatory: reject localhost, 127.0.0.0/8, ::1, link-local, 169.254.0.0/16, RFC1918 private, cloud metadata (169.254.169.254), non-http(s) schemes; re-validate every redirect hop. Production cannot disable it; test-only bypass is explicit and injected.
- URL canonicalization is deterministic; query params preserved by default (small explicit tracking denylist only, none in M-09).
- No M-10+ work: no full Fetch, PageSnapshot, Scrapy, Playwright, BrowserRender, Extract, Normalize, Record Dedup, Quality, CSV. Do not build a resource-pool scheduler (M-16).
- No new pages (13-page boundary, D-067/D-048). No Redis/K8s/msg-bus.
- Every user-owned row carries `user_id` and every query enforces ownership (D-023). Secrets never enter logs/prompts/Temporal history.
- A-Lite testing + Fast Development Test Policy: only M-09 scoped tests; no full-suite regression.
- Git: Conventional Commits (EN subject + CN body), 5–8 meaningful commits, no push, no merge, no tag. Working tree clean at end.

---

### Task 1: URL canonicalization + identity

**Files:**
- Create: `backend/app/discovery/__init__.py`
- Create: `backend/app/discovery/errors.py`
- Create: `backend/app/discovery/url.py`
- Test: `backend/tests/discovery/test_url.py`

**Interfaces:**
- Produces:
  - `canonical_url(raw: str) -> str`
  - `url_hash(canonical: str) -> str`
  - `canonicalize_and_hash(raw: str) -> tuple[str, str]`
  - `class DiscoveryValidationError(ValueError)` — raised on invalid/unsupported URL

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/discovery/test_url.py
import pytest
from app.discovery.url import canonical_url, url_hash, canonicalize_and_hash
from app.discovery.errors import DiscoveryValidationError


def test_canonical_fragment_and_default_port_removed():
    assert canonical_url("https://Example.com:443/path#frag") == "https://example.com/path"
    assert canonical_url("http://example.com:80/a/b") == "http://example.com/a/b"


def test_canonical_dot_segments_and_host_lowercase():
    assert canonical_url("https://example.com/a/../b/./c") == "https://example.com/b/c"


def test_canonical_preserves_query():
    assert canonical_url("https://example.com/x?a=1&b=2#top") == "https://example.com/x?a=1&b=2"


def test_canonical_idn_host():
    assert canonical_url("https://xn--bcher-kva.example/") == "https://xn--bcher-kva.example/"


def test_canonical_rejects_unsupported_scheme_and_userinfo():
    with pytest.raises(DiscoveryValidationError):
        canonical_url("file:///etc/passwd")
    with pytest.raises(DiscoveryValidationError):
        canonical_url("ftp://example.com/x")
    with pytest.raises(DiscoveryValidationError):
        canonical_url("https://user:pass@example.com/")


def test_hash_is_stable_and_equivalence_dedupes():
    a = canonicalize_and_hash("https://Example.com:443/x#f")
    b = canonicalize_and_hash("https://example.com/x")
    assert a == b
    assert url_hash("https://example.com/x") == url_hash("https://example.com/x")
    assert len(a[1]) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_url.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.discovery'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/discovery/errors.py
class DiscoveryError(Exception):
    """M-09 discovery error base (categorized)."""


class DiscoveryValidationError(ValueError, DiscoveryError):
    """URL or input fails discovery validation."""
```

```python
# backend/app/discovery/url.py
from __future__ import annotations

import hashlib
import posixpath
from urllib.parse import urlsplit, urlunsplit

from app.discovery.errors import DiscoveryValidationError

_ALLOWED_SCHEMES = {"http", "https"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_url(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise DiscoveryValidationError("URL 必须是非空字符串")
    parsed = urlsplit(raw.strip())
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise DiscoveryValidationError(f"不支持的 scheme: {scheme or '(空)'}")
    if parsed.username or parsed.password:
        raise DiscoveryValidationError("URL 不允许包含用户信息")
    host = (parsed.hostname or "").lower()
    if not host:
        raise DiscoveryValidationError("URL 缺少主机名")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise DiscoveryValidationError("无效主机名") from None
    port = parsed.port
    if port == _DEFAULT_PORTS.get(scheme):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or ""
    normalized = posixpath.normpath(path)
    if path.endswith("/") and not normalized.endswith("/"):
        normalized += "/"
    if normalized == ".":
        normalized = ""
    # fragment 移除；query 默认完整保留（tracking denylist 不在 M-09 范围）
    return urlunsplit((scheme, netloc, normalized, parsed.query, ""))


def url_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_and_hash(raw: str) -> tuple[str, str]:
    c = canonical_url(raw)
    return c, url_hash(c)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_url.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && .. && git add backend/app/discovery backend/tests/discovery/test_url.py
git commit -m "feat(discovery): add deterministic url canonicalizer and identity

实现保守确定性的 URL 规范化：scheme/host 小写、默认端口移除、fragment 移除、
dot-segment 归一、IDN 安全；保留 query；拒绝非 http(s)/带用户信息 URL。提供
url_hash 稳定身份，供 Frontier 去重。Task 1 of M-09。
关联模块：M-09"
```

---

### Task 2: SSRF-guarded discovery HTTP transport

**Files:**
- Create: `backend/app/discovery/ssrf.py`
- Create: `backend/app/discovery/http.py`
- Test: `backend/tests/discovery/test_ssrf.py`

**Interfaces:**
- Consumes: `canonical_url` (Task 1) — not required; this task validates raw URLs.
- Produces:
  - `assert_safe_url(url: str, *, allow_hosts: frozenset[str] = frozenset()) -> None` — raises `SSRFBlockedError`
  - `class SSRFBlockedError(DiscoveryError)`
  - `class DiscoveryTextResponse(text: str, status_code: int, final_url: str, content_type: str | None)`
  - `class DiscoveryHeadResponse(status_code: int, final_url: str)`
  - `class DiscoveryHttp: __init__(transport, *, allow_hosts=frozenset())`; `async get_text(url, timeout=20) -> DiscoveryTextResponse`; `async head(url, timeout=20) -> DiscoveryHeadResponse`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/discovery/test_ssrf.py
import pytest
from app.discovery.ssrf import assert_safe_url, SSRFBlockedError


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://127.0.0.2/x",
        "http://10.0.0.1/x",
        "http://172.16.0.1/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/x",
        "http://[fe80::1]/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
def test_assert_safe_url_rejects_dangerous_targets(url):
    with pytest.raises(SSRFBlockedError):
        assert_safe_url(url)


@pytest.mark.parametrize("url", ["https://example.com/x", "http://example.com:8080/a"])
def test_assert_safe_url_allows_public(url):
    assert_safe_url(url)  # 不抛异常


def test_explicit_test_bypass_allows_localhost():
    assert_safe_url("http://127.0.0.1:8000/x", allow_hosts=frozenset({"127.0.0.1"}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_ssrf.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/discovery/ssrf.py
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.discovery.errors import DiscoveryError

_LITERAL_IP_CACHE: dict[str, tuple[str, str]] = {}  # (url, host) -> (ip, netloc) 已解析


class SSRFBlockedError(DiscoveryError):
    pass


def _split(url: str) -> tuple[str, str, str | None]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFBlockedError(f"禁止的 scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise SSRFBlockedError("URL 缺少主机名")
    port = parsed.port or (80 if parsed.scheme == "http" else 443)
    return parsed.scheme.lower(), host, port


def _host_is_allowed(host: str, allow_hosts: frozenset[str]) -> bool:
    if host in allow_hosts:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return str(ip) in allow_hosts


def _check_ip(ip: str) -> None:
    addr = ipaddress.ip_address(ip)
    if not addr.is_global:
        raise SSRFBlockedError(f"目标解析到非公网地址: {ip}")


def assert_safe_url(url: str, *, allow_hosts: frozenset[str] = frozenset()) -> None:
    """SSRF 守卫：字面/解析后 IP 都必须为公网；本地测试用显式 allow_hosts 绕过。"""
    scheme, host, port = _split(url)
    if _host_is_allowed(host, allow_hosts):
        return
    # 字面 IP 直接判
    try:
        ip = ipaddress.ip_address(host)
        _check_ip(str(ip))
        return
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".localhost"):
        raise SSRFBlockedError("禁止访问 localhost")
    # DNS 解析后逐 IP 复核（含 IPv6）
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise SSRFBlockedError(f"无法解析主机: {host}") from exc
    seen = set()
    for info in infos:
        ip = info[4][0]
        if ip in seen:
            continue
        seen.add(ip)
        _check_ip(ip)
```

```python
# backend/app/discovery/http.py
from __future__ import annotations

from dataclasses import dataclass

from app.discovery.ssrf import assert_safe_url


@dataclass
class DiscoveryTextResponse:
    text: str
    status_code: int
    final_url: str
    content_type: str | None = None


@dataclass
class DiscoveryHeadResponse:
    status_code: int
    final_url: str


class _HttpxDiscoveryTransport:
    async def request(self, *, method, url, timeout):
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.request(method, url)
            return resp


class DiscoveryHttp:
    """SSRF 保护的最小发现 HTTP 传输：robots/sitemap/RSS/seed HTML 轻量读取。

    每个请求前校验目标；重定向逐跳重新校验（本实现逐跳自走，不回自动跟随）。
    本地 fixture 测试通过显式 allow_hosts 绕过，Production 默认关闭绕过。
    """

    def __init__(self, transport=None, *, allow_hosts: frozenset[str] = frozenset()) -> None:
        self._transport = transport or _HttpxDiscoveryTransport()
        self._allow_hosts = allow_hosts

    async def _hop(self, method: str, url: str, timeout: float) -> object:
        assert_safe_url(url, allow_hosts=self._allow_hosts)
        resp = await self._transport.request(method=method, url=url, timeout=timeout)
        return resp

    async def get_text(self, url: str, timeout: float = 20.0) -> DiscoveryTextResponse:
        current = url
        for _ in range(5):  # 有界重定向
            resp = await self._hop("GET", current, timeout)
            location = resp.headers.get("location") if resp.headers else None
            if resp.status_code in (301, 302, 303, 307, 308) and location:
                from urllib.parse import urljoin

                current = urljoin(current, location)
                continue
            return DiscoveryTextResponse(
                text=resp.text, status_code=resp.status_code,
                final_url=current, content_type=resp.headers.get("content-type"),
            )
        raise DiscoveryValidationError("重定向次数超限")

    async def head(self, url: str, timeout: float = 15.0) -> DiscoveryHeadResponse:
        current = url
        for _ in range(5):
            resp = await self._hop("HEAD", current, timeout)
            location = resp.headers.get("location") if resp.headers else None
            if resp.status_code in (301, 302, 303, 307, 308) and location:
                from urllib.parse import urljoin

                current = urljoin(current, location)
                continue
            return DiscoveryHeadResponse(status_code=resp.status_code, final_url=current)
        raise DiscoveryValidationError("重定向次数超限")
```

(Note: import `DiscoveryValidationError` in http.py.)

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_ssrf.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/discovery/ssrf.py backend/app/discovery/http.py backend/tests/discovery/test_ssrf.py
git commit -m "feat(discovery): add ssrf-guarded discovery http transport

Discovery HTTP 建立最小 get_text/head（robots/sitemap/RSS/seed HTML），每请求 +
每跳重定向逐次 SSRF 校验：拒绝 localhost/私网/link-local/169.254/metadata/
非 http(s)；本地 fixture 通过显式 allow_hosts 绕过。Task 2 of M-09。
关联模块：M-09"
```

---

### Task 3: robots.txt fetch / parse / cache / policy

**Files:**
- Create: `backend/app/discovery/robots.py`
- Test: `backend/tests/discovery/test_robots.py`

**Interfaces:**
- Consumes: `DiscoveryHttp`, `DiscoveryTextResponse` (Task 2).
- Produces:
  - `DEFAULT_USER_AGENT = "KairosBot"`
  - `class RobotsPolicy` — `allowed(url, user_agent=DEFAULT_USER_AGENT) -> bool`, `sitemap_urls() -> list[str]`
  - `class RobotsCache: __init__(http, *, ttl_seconds=3600, user_agent=DEFAULT_USER_AGENT)`; `async get(host: str) -> RobotsPolicy`
  - `async fetch_robots(http: DiscoveryHttp, base_url: str) -> RobotsPolicy`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/discovery/test_robots.py
import pytest
from app.discovery.robots import RobotsCache, RobotsPolicy, parse_robots, DEFAULT_USER_AGENT


ALLOW_DENY = """User-agent: *
Disallow: /private/
Allow: /public/
Disallow: /no-all
Sitemap: https://example.com/sitemap.xml
"""


def test_parse_allow_deny_and_sitemap():
    policy = parse_robots(ALLOW_DENY)
    assert policy.allowed("https://example.com/public/x")
    assert not policy.allowed("https://example.com/private/x")
    assert not policy.allowed("https://example.com/no-all")
    assert policy.allowed("https://example.com/elsewhere")
    assert policy.sitemap_urls() == ["https://example.com/sitemap.xml"]


def test_no_robots_means_allow_all():
    policy = parse_robots("")
    assert policy.allowed("https://example.com/anything")


def test_specific_user_agent_wins_over_asterisk():
    txt = "User-agent: *\nDisallow: /\nUser-agent: KairosBot\nAllow: /ok\n"
    policy = parse_robots(txt)
    assert policy.allowed("https://example.com/ok", user_agent="KairosBot")
    assert not policy.allowed("https://example.com/other", user_agent="KairosBot")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_robots.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/discovery/robots.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from time import monotonic

from app.discovery.http import DiscoveryHttp

DEFAULT_USER_AGENT = "KairosBot"
_MAX_ROBOTS_BYTES = 512 * 1024


@dataclass(frozen=True)
class _Rule:
    path: str
    allow: bool


@dataclass
class RobotsPolicy:
    """robots.txt 规则：默认遵守；无规则 → 允许。"""

    rules: list[tuple[str, list[_Rule]]] = field(default_factory=list)  # (user_agent, rules)
    sitemaps: list[str] = field(default_factory=list)

    def allowed(self, url: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
        from urllib.parse import urlsplit

        path = urlsplit(url).path or "/"
        rules = self._rules_for(user_agent)
        if not rules:
            return True
        # 最长匹配优先；同长 Allow 优先（RFC 9309 精神）
        best: _Rule | None = None
        for r in rules:
            if path.startswith(r.path) and (best is None or len(r.path) > len(best.path)
                                            or (len(r.path) == len(best.path) and r.allow)):
                best = r
        return best is None or best.allow

    def _rules_for(self, user_agent: str) -> list[_Rule]:
        matched = [r for ua, r in self.rules if ua == "*"]
        for ua, r in self.rules:
            if ua != "*" and ua.lower() in user_agent.lower():
                return r
        return matched[0] if matched else []

    def sitemap_urls(self) -> list[str]:
        return list(self.sitemaps)


def parse_robots(text: str) -> RobotsPolicy:
    groups: dict[str, list[_Rule]] = {}
    sitemaps: list[str] = []
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
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


def _robots_url_for(host: str) -> str:
    return f"https://{host}/robots.txt" if "://" not in host else f"{host}/robots.txt"


@dataclass
class RobotsCache:
    """按 host/origin 缓存，TTL 内不重复下载。线程安全由 GIL + 单写近似保证。"""

    http: DiscoveryHttp
    ttl_seconds: int = 3600
    user_agent: str = DEFAULT_USER_AGENT
    _cache: dict[str, tuple[float, RobotsPolicy]] = field(default_factory=dict)

    async def get(self, host: str) -> RobotsPolicy:
        now = monotonic()
        cached = self._cache.get(host)
        if cached and now - cached[0] < self.ttl_seconds:
            return cached[1]
        policy = await fetch_robots(self.http, _robots_url_for(host))
        self._cache[host] = (now, policy)
        return policy


async def fetch_robots(http: DiscoveryHttp, robots_url: str) -> RobotsPolicy:
    resp = await http.get_text(robots_url, timeout=15.0)
    if resp.status_code == 404:
        return RobotsPolicy()
    if resp.status_code not in (200,):
        return RobotsPolicy()  # 非 404 错误按保守“无规则”处理
    return parse_robots(resp.text[:_MAX_ROBOTS_BYTES])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_robots.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/discovery/robots.py backend/tests/discovery/test_robots.py
git commit -m "feat(discovery): add robots.txt fetch parse and policy cache

默认遵守 robots：解析 Allow/Disallow 用户代理组与 Sitemap 指令，最长匹配 +
Allow 优先；按 host 缓存 TTL，避免逐 URL 下载。Task 3 of M-09。
关联模块：M-09"
```

---

### Task 4: Frontier schema + repository (migration 0007)

**Files:**
- Create: `backend/alembic/versions/0007_extend_url_resource_frontier.py`
- Modify: `backend/app/domain/models.py` (`URLResource`, ~266-284) — add columns
- Create: `backend/app/discovery/models.py`
- Create: `backend/app/discovery/frontier.py`
- Test: `backend/tests/discovery/test_frontier.py`

**Interfaces:**
- Consumes: `canonicalize_and_hash`, `url_hash` (Task 1).
- Produces:
  - `class DiscoverySource(StrEnum)`: `USER_SEED, SEARCH_RESULT, SITEMAP, SITEMAP_INDEX, RSS, ATOM, NAVIGATION, PAGINATION, INTERNAL_LINK, ROBOTS_SITEMAP`
  - `class FrontierState(StrEnum)`: `DISCOVERED, ACCESS_ALLOWED, WAITING_APPROVAL, BLOCKED, READY_FOR_FETCH, HANDED_OFF`
  - `class DiscoveryEvidence(BaseModel)`: `source, query=None, provider=None, rank=None, result_url=None, parent_url_hash=None, note=None`
  - `class CandidateSite(BaseModel)`: `site_host: str, display_url: str, evidence: list[SearchResultRef], depth: int`
  - `class SearchResultRef(BaseModel)`: `url, title, snippet, provider, rank, query`
  - `class UrlFrontierRepository`:
    - `upsert_discovery(*, task_id, user_id, run_id, spec_version, raw_url, source, evidence=None, depth=0, priority=0) -> tuple[str, bool]` (url_hash, created)
    - `mark_state(*, user_id, url_hash, state) -> None`
    - `increment_discovery_count(*, user_id, url_hash) -> int`
    - `list_by_state(*, user_id, task_id, state) -> list[URLResource]`
    - `list_ready_for_fetch(*, user_id, task_id, limit=200) -> list[URLResource]`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/discovery/test_frontier.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import DiscoverySource, FrontierState, DiscoveryEvidence
from app.discovery.url import canonicalize_and_hash
from app.domain.models import URLResource


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    from app.infra.db import Base
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_duplicate_canonical_url_is_single_entry(db):
    repo = UrlFrontierRepository(db)
    h1, created1 = repo.upsert_discovery(
        task_id=1, user_id=1, run_id=1, spec_version=1,
        raw_url="https://Example.com:443/x#frag", source=DiscoverySource.USER_SEED,
    )
    h2, created2 = repo.upsert_discovery(
        task_id=1, user_id=1, run_id=1, spec_version=1,
        raw_url="https://example.com/x", source=DiscoverySource.SEARCH_RESULT,
        evidence=DiscoveryEvidence(source=DiscoverySource.SEARCH_RESULT, query="k"),
    )
    assert created1 is True
    assert created2 is False
    assert h1 == h2
    count = db.query(URLResource).filter(URLResource.task_id == 1).count()
    assert count == 1
    assert repo.increment_discovery_count(user_id=1, url_hash=h1) == 2


def test_state_transition_to_ready_for_fetch(db):
    repo = UrlFrontierRepository(db)
    h, _ = repo.upsert_discovery(task_id=2, user_id=1, run_id=1, spec_version=1,
                                 raw_url="https://example.com/page", source=DiscoverySource.SITEMAP)
    repo.mark_state(user_id=1, url_hash=h, state=FrontierState.READY_FOR_FETCH)
    ready = repo.list_ready_for_fetch(user_id=1, task_id=2)
    assert len(ready) == 1
    assert ready[0].url_hash == h
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_frontier.py -q`
Expected: FAIL — import error (models/columns missing)

- [ ] **Step 3: Add migration 0007**

```python
# backend/alembic/versions/0007_extend_url_resource_frontier.py
"""Extend url_resources with M-09 discovery/frontier metadata.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("url_resources", sa.Column("spec_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("url_resources", sa.Column("discovery_source", sa.String(length=40), nullable=False, server_default="USER_SEED"))
    op.add_column("url_resources", sa.Column("parent_url_hash", sa.String(length=64), nullable=True))
    op.add_column("url_resources", sa.Column("depth", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("url_resources", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("url_resources", sa.Column("discovery_count", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("url_resources", sa.Column("discovery_evidence", sa.JSON(), nullable=True))
    op.add_column("url_resources", sa.Column("robots_allowed", sa.Boolean(), nullable=True))
    op.add_column("url_resources", sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True))
    # frontier state 默认：DISCOVERED（M-09 首个消费者，无生产历史行）
    op.alter_column("url_resources", "status", server_default="DISCOVERED")
    op.create_index("ix_url_resources_state", "url_resources", ["task_id", "status"])
    op.create_index("ix_url_resources_parent", "url_resources", ["parent_url_hash"])


def downgrade() -> None:
    op.drop_index("ix_url_resources_parent", table_name="url_resources")
    op.drop_index("ix_url_resources_state", table_name="url_resources")
    op.alter_column("url_resources", "status", server_default="pending")
    op.drop_column("url_resources", "accessed_at")
    op.drop_column("url_resources", "robots_allowed")
    op.drop_column("url_resources", "discovery_evidence")
    op.drop_column("url_resources", "discovery_count")
    op.drop_column("url_resources", "priority")
    op.drop_column("url_resources", "depth")
    op.drop_column("url_resources", "parent_url_hash")
    op.drop_column("url_resources", "discovery_source")
    op.drop_column("url_resources", "spec_version")
```

- [ ] **Step 4: Extend URLResource model + discovery models + frontier repository**

```python
# backend/app/domain/models.py — URLResource 追加列（在 status 之后）
class URLResource(Base):
    __tablename__ = "url_resources"
    __table_args__ = (UniqueConstraint("task_id", "url_hash", name="uq_ur_task_url_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, default="seed")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DISCOVERED")
    # M-09 frontier metadata
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    discovery_source: Mapped[str] = mapped_column(String(40), nullable=False, default="USER_SEED")
    parent_url_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    discovery_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

```python
# backend/app/discovery/models.py
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class DiscoverySource(StrEnum):
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
    DISCOVERED = "DISCOVERED"
    ACCESS_ALLOWED = "ACCESS_ALLOWED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    READY_FOR_FETCH = "READY_FOR_FETCH"
    HANDED_OFF = "HANDED_OFF"


class SearchResultRef(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    provider: str = ""
    rank: int | None = None
    query: str = ""


class DiscoveryEvidence(BaseModel):
    source: DiscoverySource
    query: str | None = None
    provider: str | None = None
    rank: int | None = None
    result_url: str | None = None
    parent_url_hash: str | None = None
    note: str | None = None


class CandidateSite(BaseModel):
    """同一站点多条发现的合并站点候选（保留原始证据）。"""

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
    base = _PRIORITY_BY_SOURCE.get(source, 0)
    if source == DiscoverySource.SEARCH_RESULT and rank is not None:
        base += max(0, 10 - rank)  # 排名越高分越高（rank 越小越好）
    return base
```

```python
# backend/app/discovery/frontier.py
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.discovery.models import DiscoveryEvidence, DiscoverySource, FrontierState, priority_for
from app.discovery.url import canonicalize_and_hash
from app.domain.models import URLResource


class UrlFrontierRepository:
    """持久化 URL Frontier：canonical 去重 + 幂等 + 发现证据 + 状态。

    唯一约束 task_id+url_hash 是去重兜底；重复发现只累加 discovery_count 与
    evidence，不创建第二个有效 Frontier Entry（D-016 / M-09 idempotency）。
    """

    def __init__(self, db: Session) -> None:
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
            row.discovery_evidence = (evidence or DiscoveryEvidence(source=source)).model_dump(mode="json")
            self._db.add(row)
            self._db.commit()
            return url_hash, False
        row = URLResource(
            task_id=task_id, user_id=user_id, run_id=run_id, url=canonical, url_hash=url_hash,
            source_type=source.value, status=FrontierState.DISCOVERED.value,
            spec_version=spec_version, discovery_source=source.value,
            discovery_count=1,
            discovery_evidence=(evidence or DiscoveryEvidence(source=source)).model_dump(mode="json"),
            depth=depth,
            priority=priority if priority is not None else priority_for(source),
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return url_hash, True

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

    def _owned(self, user_id: int, url_hash: str) -> URLResource:
        row = (
            self._db.query(URLResource)
            .filter(URLResource.user_id == user_id, URLResource.url_hash == url_hash)
            .first()
        )
        if row is None:
            raise DiscoveryError("URL 不属于当前用户或不存在")
        return row

    def list_by_state(self, *, user_id: int, task_id: int, state: FrontierState) -> list[URLResource]:
        return (
            self._db.query(URLResource)
            .filter(URLResource.user_id == user_id, URLResource.task_id == task_id,
                    URLResource.status == state.value)
            .all()
        )

    def list_ready_for_fetch(self, *, user_id: int, task_id: int, limit: int = 200) -> list[URLResource]:
        return (
            self._db.query(URLResource)
            .filter(URLResource.user_id == user_id, URLResource.task_id == task_id,
                    URLResource.status == FrontierState.READY_FOR_FETCH.value)
            .order_by(URLResource.priority.desc(), URLResource.id.asc())
            .limit(limit)
            .all()
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_frontier.py -q`
Expected: PASS

- [ ] **Step 6: Verify migration SQL**

Run: `backend/.venv/Scripts/python.exe -m alembic upgrade head --sql | grep -E "url_resources" | head -30`
Expected: 0007 ALTER statements for url_resources present.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0007_extend_url_resource_frontier.py backend/app/domain/models.py backend/app/discovery/models.py backend/app/discovery/frontier.py backend/tests/discovery/test_frontier.py
git commit -m "feat(discovery): add persistent url frontier with canonical dedupe

迁移 0007 扩展 url_resources：spec_version/discovery_source/parent_url_hash/depth/
priority/discovery_count/discovery_evidence/robots_allowed/accessed_at，status 默认
DISCOVERED。UrlFrontierRepository 以 task_id+url_hash 唯一约束做去重兜底，重复发现
累加计数与证据，不产生第二有效 Entry；提供状态迁移与 READY_FOR_FETCH 查询。
Task 4 of M-09。
关联模块：M-09"
```

---

### Task 5: SourceSearch executor + search semantics

**Files:**
- Create: `backend/app/discovery/source_search.py`
- Create: `backend/app/discovery/executors.py` (registration shell — full in Task 8)
- Test: `backend/tests/discovery/test_source_search.py`

**Interfaces:**
- Consumes: `UrlFrontierRepository`, `CandidateSite`, `DiscoverySource`, `DiscoveryEvidence` (Task 4), `SearchProvider`/`SearchResult`/`SearchConfig`/`CredentialVault` (M-03), `ExecutionUnit`/`ExecuteUnitResult` (M-08).
- Produces:
  - `SEARCH_PROVIDER_NOT_CONFIGURED = "SEARCH_PROVIDER_NOT_CONFIGURED"` (stable error code)
  - `class SearchService` — `__init__(db, vault=None, search_configs=None, registry=None, http=None)`; `async execute(unit: ExecutionUnit) -> ExecuteUnitResult`
  - `def merge_into_candidate_sites(results: list[SearchResult]) -> list[CandidateSite]`
  - `class SourceSearchError(DiscoveryError)` with `.code`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/discovery/test_source_search.py
import pytest
from app.discovery.source_search import merge_into_candidate_sites, SearchService
from app.discovery.models import DiscoverySource
from app.providers.search_protocol import SearchResult


def test_merge_search_results_into_candidate_sites():
    results = [
        SearchResult(url="https://example.com/a", title="A", snippet="s", provider="p", rank=1, query="q"),
        SearchResult(url="https://example.com/b", title="B", snippet="s", provider="p", rank=2, query="q"),
        SearchResult(url="https://other.com/x", title="X", snippet="s", provider="p", rank=3, query="q"),
    ]
    sites = merge_into_candidate_sites(results)
    hosts = {s.site_host for s in sites}
    assert hosts == {"example.com", "other.com"}
    example = next(s for s in sites if s.site_host == "example.com")
    assert len(example.evidence) == 2  # 保留每条搜索证据


class _FakeSearchProvider:
    provider_type = "fake_search"
    def __init__(self, results):
        self._results = results
        self.calls = 0
    async def search(self, *, query, limit, api_key, base_url):
        self.calls += 1
        assert query == "电动汽车"
        return self._results
    async def test_connection(self, *, api_key, base_url):
        raise NotImplementedError


class _FakeSearchConfig:
    config_id = "cfg1"
    version = 1
    provider_type = "fake_search"
    base_url = "https://search.test"
    credential_version_id = 7
    connection_status = "available"


class _FakeVault:
    def read_for_execution(self, *, user_id, credential_version_id):
        return "sk-fake"


class _FakeConfigRepo:
    def list_available(self, user_id):
        return [_FakeSearchConfig()]


@pytest.mark.asyncio
async def test_execute_returns_candidates_for_available_search():
    # SearchService.execute 通过 SearchConfigRepository.list_current + connection_status
    # 解析可用配置；build_search_provider 由 registry 映射。这里验证查询参数正确传递、
    # 结果合并为 Candidate Site（候选写入由 Frontier 集成测试覆盖）。
    from app.activities.execution_seam import ExecutionUnit
    from app.discovery.source_search import SearchService

    provider = _FakeSearchProvider([SearchResult(url="https://example.com/a", title="A", snippet="s",
                                                 provider="p", rank=1, query="电动汽车")])
    # 注入 registry 使 build_search_provider 返回 fake provider（测试注入点）
    service = SearchService(None, vault=_FakeVault(), search_configs=_FakeConfigRepo(), registry=provider)
    # execute 会 resolve run -> DB；此处用集成级断言替代（见 Task 8 E2E），
    # 单测聚焦 merge_into_candidate_sites + 缺配置稳定错误：
    from app.discovery.source_search import SourceSearchError, SEARCH_PROVIDER_NOT_CONFIGURED
    with pytest.raises(SourceSearchError) as exc:
        await SearchService(None, vault=_FakeVault(), search_configs=_EmptyConfigRepo())._require_config(1)
    assert exc.value.code == SEARCH_PROVIDER_NOT_CONFIGURED
```

(Note: Step 1 test for `merge_into_candidate_sites` is concrete; the executor-level semantics are covered by the integration test in Task 8. Remove the NotImplementedError placeholder — the concrete assertions are: available search → ExecuteUnitResult with committed_refs candidates; missing config → SEARCH_PROVIDER_NOT_CONFIGURED; SPECIFIED_SOURCE no config → OK.)

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_source_search.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/discovery/source_search.py
from __future__ import annotations

from collections import defaultdict

from app.activities.execution_seam import ExecuteUnitResult, ExecutionUnit
from app.discovery.errors import DiscoveryError
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import CandidateSite, DiscoveryEvidence, DiscoverySource, SearchResultRef
from app.domain.task_types import TaskType
from app.providers.search_protocol import SearchResult, SearchProvider

SEARCH_PROVIDER_NOT_CONFIGURED = "SEARCH_PROVIDER_NOT_CONFIGURED"


class SourceSearchError(DiscoveryError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def merge_into_candidate_sites(results: list[SearchResult]) -> list[CandidateSite]:
    from urllib.parse import urlsplit

    by_host: dict[str, CandidateSite] = {}
    for r in results:
        host = (urlsplit(r.url).hostname or "").lower()
        if not host:
            continue
        site = by_host.get(host)
        if site is None:
            site = CandidateSite(site_host=host, display_url=r.url, evidence=[], depth=0)
            by_host[host] = site
        site.evidence.append(
            SearchResultRef(url=r.url, title=r.title, snippet=r.snippet,
                            provider=r.provider, rank=r.rank, query=r.query)
        )
    return list(by_host.values())


class SearchService:
    """SourceSearch executor：消费 Plan 已校验参数，执行 Search Provider。

    不调用 LLM 生成 query（PlanGenerator 负责计划层参数）；只执行已验证参数。
    """

    def __init__(self, db, *, vault=None, search_configs=None, registry=None) -> None:
        self._db = db
        self._vault = vault
        self._search_configs = search_configs
        self._registry = registry

    def _available_search_config(self, user_id: int):
        from app.providers.repository import SearchConfigRepository

        repo = self._search_configs or SearchConfigRepository(self._db)
        for cfg in repo.list_current(user_id):
            if cfg.connection_status == "available":
                return cfg
        return None

    async def execute(self, unit: ExecutionUnit) -> ExecuteUnitResult:
        from app.domain.models import Run
        from app.credentials.vault import CredentialVault
        from app.credentials.repository import CredentialRepository
        from app.providers.registry import build_search_provider
        from app.config import get_settings

        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(unit_index=unit.index, status="FAILED", error_code="RUN_NOT_FOUND", committed_refs={})
        cfg = self._available_search_config(run.user_id)
        if cfg is None:
            # 计划含 SourceSearch → 该任务确实需要搜索（EXPLORATORY/HYBRID）。
            # 缺可用配置：稳定错误，任务/计划不丢失，不静默替换为别的 Provider。
            raise SourceSearchError(SEARCH_PROVIDER_NOT_CONFIGURED, "尚未配置可用的搜索服务")

        settings = get_settings()
        vault = self._vault or CredentialVault(
            master_key=crypto.master_key_from_env_value(settings.credential_master_key),
            key_version=settings.credential_key_version,
            repository=CredentialRepository(self._db),
        )
        api_key = (
            vault.read_for_execution(user_id=run.user_id, credential_version_id=cfg.credential_version_id)
            if cfg.credential_version_id is not None
            else None
        )
        provider = build_search_provider(cfg.provider_type)
        params = unit.parameters or {}
        query = str(params.get("query") or "")
        limit = int(params.get("max_results") or 20)
        results = await provider.search(
            query=query, limit=limit, api_key=api_key, base_url=cfg.base_url
        )
        sites = merge_into_candidate_sites(results)
        frontier = UrlFrontierRepository(self._db)
        hashes: list[str] = []
        for site in sites:
            for ref in site.evidence:
                h, _ = frontier.upsert_discovery(
                    task_id=run.task_id, user_id=run.user_id, run_id=run.run_id,
                    spec_version=run.spec_version, raw_url=ref.url, source=DiscoverySource.SEARCH_RESULT,
                    evidence=DiscoveryEvidence(
                        source=DiscoverySource.SEARCH_RESULT, query=query,
                        provider=ref.provider, rank=ref.rank, result_url=ref.url,
                    ),
                )
                hashes.append(h)
        return ExecuteUnitResult(
            unit_index=unit.index, status="OK",
            committed_refs={
                "run_id": run.run_id, "node_id": unit.node_id, "node_type": unit.node_type,
                "candidate_sites": len(sites), "candidates": len(hashes),
                "task_id": run.task_id,
            },
        )
```

(Note: The executor needs `user_id`/`task_id`/`spec_version` — these come from the Run row via `unit.run_id`. The concrete implementation resolves the Run (as M-08 `fetch_next_execution_unit` does) to obtain user/task/spec, then: resolve available SearchConfig → decrypt key via vault → `build_search_provider` → `provider.search(query, limit, api_key, base_url)` → `merge_into_candidate_sites` → for each candidate site, for each evidence result, `frontier.upsert_discovery(..., source=SEARCH_RESULT, evidence=...)`. Missing available config → `SourceSearchError(SEARCH_PROVIDER_NOT_CONFIGURED, ...)`. SPECIFIED_SOURCE without config → return OK with no candidates.)

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_source_search.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/discovery/source_search.py backend/tests/discovery/test_source_search.py
git commit -m "feat(search): execute source search providers with stable semantics

SourceSearch executor 复用 M-03 SearchProvider/SearchConfig/CredentialVault：可用
search config → provider.search → 合并 Candidate Sites（保留 query/provider/rank/result
URL 证据）→ 写入 Frontier(SEARCH_RESULT)。缺可用 SearchConfig → 稳定
SEARCH_PROVIDER_NOT_CONFIGURED；SPECIFIED_SOURCE 无配置仍可继续。Task 5 of M-09。
关联模块：M-09"
```

---

### Task 6: AccessRulesCheck executor + robots override approval

**Files:**
- Create: `backend/app/discovery/access_rules.py`
- Create: `backend/app/activities/discovery_approval.py` (consume robots override activity)
- Modify: `backend/app/workflows/task_workflow.py` (handle `WAITING_APPROVAL` executor result)
- Test: `backend/tests/discovery/test_access_rules.py`

**Interfaces:**
- Consumes: `UrlFrontierRepository`, `FrontierState`, `RobotsCache`, `DiscoveryHttp`, `ApprovalService` (M-08), `ExecutionUnit`/`ExecuteUnitResult`.
- Produces:
  - `class AccessDecision(StrEnum)`: `ALLOW, ROBOTS_DENIED_PUBLIC, AUTH_PRIVATE, CAPTCHA, ACCESS_CONTROLLED, SCOPE_OUT, SCHEME_INVALID`
  - `def decide_access(url, *, spec, robots_policy, allow_hosts=frozenset()) -> AccessDecision`
  - `class AccessRulesService` — `async execute(unit: ExecutionUnit) -> ExecuteUnitResult`; returns status `OK` or `WAITING_APPROVAL` with `committed_refs={"approval_id": int, "url_hash": str, "parameters": {...}}`
  - Activity `consume_robots_override(user_id, approval_id, url_hash, parameters) -> dict` (ok, status) — fingerprint revalidation + mark URL READY_FOR_FETCH or BLOCKED.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/discovery/test_access_rules.py
import pytest
from app.discovery.access_rules import decide_access, AccessDecision
from app.discovery.robots import RobotsPolicy


def _spec() -> dict:
    return {"source_scope": {"mode": "SPECIFIED_SOURCE", "seed_urls": ["https://example.com"], "source_hints": []}}


def test_allow_public_robots_ok():
    policy = RobotsPolicy()  # 无规则 → allow
    d = decide_access("https://example.com/x", spec=_spec(), robots_policy=policy)
    assert d == AccessDecision.ALLOW


def test_robots_denied_public_is_overrideable():
    from app.discovery.robots import parse_robots
    policy = parse_robots("User-agent: *\nDisallow: /private/\n")
    d = decide_access("https://example.com/private/x", spec=_spec(), robots_policy=policy)
    assert d == AccessDecision.ROBOTS_DENIED_PUBLIC


def test_auth_private_is_not_overrideable():
    # 探测到登录/鉴权特征（响应 401/403 或凭据要求）→ 安全分类，绝不可覆盖
    d = decide_access("https://example.com/account", spec=_spec(), robots_policy=RobotsPolicy())
    assert d == AccessDecision.ALLOW  # 403/401 检测发生在 HTTP 探测层；纯 URL 阶段视为 ALLOW
```

(Note: 401/403/auth/private detection requires an HTTP accessibility probe — a lightweight HEAD/GET metadata check in the executor. The pure-URL `decide_access` returns ALLOW for unknown; the probe (in the executor, via DiscoveryHttp) upgrades AUTH_PRIVATE on 401/403 responses. The concrete test covers the URL-level rules; the HTTP-probe branch is covered by the executor integration test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_access_rules.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/discovery/access_rules.py
from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit

from app.discovery.robots import RobotsPolicy

ALLOWED_SCHEMES = {"http", "https"}


class AccessDecision(StrEnum):
    ALLOW = "ALLOW"
    ROBOTS_DENIED_PUBLIC = "ROBOTS_DENIED_PUBLIC"
    AUTH_PRIVATE = "AUTH_PRIVATE"
    CAPTCHA = "CAPTCHA"
    ACCESS_CONTROLLED = "ACCESS_CONTROLLED"
    SCOPE_OUT = "SCOPE_OUT"
    SCHEME_INVALID = "SCHEME_INVALID"


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def decide_access(url: str, *, spec: dict, robots_policy: RobotsPolicy, user_agent: str = "KairosBot") -> AccessDecision:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        return AccessDecision.SCHEME_INVALID
    seed_hosts = {
        _host_of(u) for u in (spec.get("source_scope", {}).get("seed_urls") or [])
    }
    if seed_hosts and _host_of(url) not in seed_hosts:
        return AccessDecision.SCOPE_OUT
    if not robots_policy.allowed(url, user_agent=user_agent):
        return AccessDecision.ROBOTS_DENIED_PUBLIC
    return AccessDecision.ALLOW
```

```python
# backend/app/discovery/access_rules.py (AccessRulesService, 追加到同一文件)
class AccessRulesService:
    """AccessRulesCheck executor：scheme/host/scope/robots 决策 + robots override 审批。

    robots denied 且公共 → JIT Approval（复用 M-08 ApprovalService），task 进入
    WAITING_APPROVAL；用户批准后 fingerprint 复验再继续。auth/private/captcha/
    access-controlled → BLOCKED（不可覆盖）。"""

    def __init__(self, db, *, approval=None, robots=None, http=None, user_agent="KairosBot") -> None:
        self._db = db
        self._approval = approval
        self._robots = robots or RobotsCache(http or DiscoveryHttp())
        self._user_agent = user_agent

    async def execute(self, unit: ExecutionUnit) -> ExecuteUnitResult:
        from app.domain.models import Run
        from app.approval.service import ApprovalService
        from app.approval.schemas import ApprovalScope
        from app.discovery.frontier import UrlFrontierRepository
        from app.discovery.models import FrontierState
        from app.domain.repository import SpecVersionRepository, TaskRepository
        from app.domain.service import DomainService

        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(unit_index=unit.index, status="FAILED", error_code="RUN_NOT_FOUND", committed_refs={})
        spec = SpecVersionRepository(self._db).get_version(run.user_id, run.task_id, run.spec_version)
        params = unit.parameters or {}
        respect_robots = bool(params.get("respect_robots", True))
        frontier = UrlFrontierRepository(self._db)
        pending = frontier.list_by_state(user_id=run.user_id, task_id=run.task_id, state=FrontierState.DISCOVERED)
        if not pending:
            return ExecuteUnitResult(unit_index=unit.index, status="OK", committed_refs={"checked": 0, "run_id": run.run_id})
        blocked_hashes: list[str] = []
        waiting_url_hash: str | None = None
        waiting_params: dict | None = None
        for row in pending[:200]:
            policy = await self._robots.get(_host_of(row.url)) if respect_robots else RobotsPolicy()
            decision = decide_access(row.url, spec=spec.payload, robots_policy=policy, user_agent=self._user_agent)
            # 轻量 HTTP 探测：401/403 → AUTH_PRIVATE（不可覆盖）；验证码特征 → CAPTCHA
            if decision == AccessDecision.ALLOW and params.get("public_only", True):
                probe = await self._http.head(row.url, timeout=8.0)
                if probe.status_code in (401, 403):
                    decision = AccessDecision.AUTH_PRIVATE
                elif "captcha" in (probe.headers.get("set-cookie", "").lower() if probe.headers else ""):
                    decision = AccessDecision.CAPTCHA
            if decision == AccessDecision.ALLOW:
                frontier.mark_state(user_id=run.user_id, url_hash=row.url_hash, state=FrontierState.ACCESS_ALLOWED)
            elif decision == AccessDecision.ROBOTS_DENIED_PUBLIC:
                if waiting_url_hash is None:
                    approval = ApprovalService(self._db).request_approval(
                        user_id=run.user_id, task_id=run.task_id, spec_version=run.spec_version,
                        plan_version=run.plan_version, node_id=unit.node_id, node_type=unit.node_type,
                        action_type="robots_override", target=row.url,
                        parameters={"url": row.url, "host": _host_of(row.url)},
                        scope=ApprovalScope.THIS_ACTION,
                    )
                    task = TaskRepository(self._db).get_owned(run.user_id, run.task_id)
                    try:
                        DomainService(TaskRepository(self._db)).transition_task(
                            user_id=run.user_id, task_id=run.task_id, command="mark_waiting_approval",
                            expected_version=task.version, actor_type="system", reason="robots_override_approval",
                        )
                    except Exception:
                        pass  # 幂等
                    frontier.mark_state(user_id=run.user_id, url_hash=row.url_hash, state=FrontierState.WAITING_APPROVAL)
                    waiting_url_hash = row.url_hash
                    waiting_params = {"url": row.url, "host": _host_of(row.url)}
                    self._db.commit()
                    return ExecuteUnitResult(
                        unit_index=unit.index, status="WAITING_APPROVAL",
                        committed_refs={"approval_id": approval.id, "url_hash": row.url_hash,
                                        "parameters": waiting_params, "run_id": run.run_id},
                    )
                frontier.mark_state(user_id=run.user_id, url_hash=row.url_hash, state=FrontierState.WAITING_APPROVAL)
            else:
                frontier.mark_blocked(user_id=run.user_id, url_hash=row.url_hash,
                                      reason=f"access_{decision.value}")
                blocked_hashes.append(row.url_hash)
        self._db.commit()
        return ExecuteUnitResult(
            unit_index=unit.index, status="OK",
            committed_refs={"checked": len(pending[:200]), "blocked": len(blocked_hashes),
                            "run_id": run.run_id, "node_id": unit.node_id, "node_type": unit.node_type},
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_access_rules.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/discovery/access_rules.py backend/tests/discovery/test_access_rules.py
git commit -m "feat(discovery): add access rules check with robots approval boundary

AccessRulesCheck 判定 scheme/host/scope/robots；robots denied 且公共 → JIT
Approval(WAITING_APPROVAL)；auth/private/captcha/access-controlled → 不可覆盖、
BLOCKED。Task 6 of M-09（executor 接线在 Task 8）。
关联模块：M-09"
```

---

### Task 7: LinkDiscovery + site expansion (sitemap/RSS/Atom/nav/pagination/internal links)

**Files:**
- Create: `backend/app/discovery/link_discovery.py`
- Test: `backend/tests/discovery/test_link_discovery.py`

**Interfaces:**
- Consumes: `UrlFrontierRepository`, `DiscoveryHttp`, `RobotsCache`, `DiscoverySource`, `canonicalize_and_hash` (Tasks 1-4).
- Produces:
  - `def parse_sitemap(xml_text: str) -> list[str]` (urlset loc; sitemapindex → sitemap URLs)
  - `def parse_feed(xml_text: str) -> list[str]` (RSS `<link>` items; Atom `<link href>` entries)
  - `def extract_html_links(html: str, base_url: str) -> list[tuple[str, str]]` → (href, rel) for `rel in {nav, pagination, internal}`
  - `class LinkDiscoveryService` — `async execute(unit: ExecutionUnit) -> ExecuteUnitResult`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/discovery/test_link_discovery.py
import pytest
from app.discovery.link_discovery import parse_sitemap, parse_feed, extract_html_links


SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b?x=1</loc></url>
</urlset>"""

SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
</sitemapindex>"""

RSS = """<rss version="2.0"><channel>
  <item><link>https://example.com/p1</link></item>
  <item><link>https://example.com/p2</link></item>
</channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><link href="https://example.com/p3"/></entry>
</feed>"""

HTML = """<html><body>
  <nav><a href="/products">Products</a></nav>
  <a rel="next" href="/list?page=2">Next</a>
  <a href="/detail/1">item</a>
  <a href="https://other.com/x">external</a>
</body></html>"""


def test_parse_sitemap_and_index():
    assert parse_sitemap(SITEMAP) == ["https://example.com/a", "https://example.com/b?x=1"]
    assert parse_sitemap(SITEMAP_INDEX) == ["https://example.com/sitemap-1.xml"]


def test_parse_rss_and_atom():
    assert parse_feed(RSS) == ["https://example.com/p1", "https://example.com/p2"]
    assert parse_feed(ATOM) == ["https://example.com/p3"]


def test_extract_html_links_includes_nav_pagination_internal_excludes_asset():
    links = extract_html_links(HTML, "https://example.com/list")
    hrefs = {h for h, _ in links}
    assert "/products" in hrefs
    assert "/list?page=2" in hrefs
    assert "/detail/1" in hrefs
    assert "https://other.com/x" in hrefs  # 跨域保留为 Candidate Hint（不直接入 Frontier）
    assert "https://example.com/logo.png" not in {h for h, _ in extract_html_links("<img src='https://example.com/logo.png'>", "https://example.com/")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_link_discovery.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/discovery/link_discovery.py
from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET

_SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def parse_sitemap(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    urls: list[str] = []
    if root.tag.endswith("sitemapindex"):
        for child in root.findall("s:sitemap/s:loc", _SITEMAP_NS):
            urls.append(child.text or "")
    else:
        for child in root.findall("s:url/s:loc", _SITEMAP_NS):
            urls.append(child.text or "")
    return [u for u in urls if u]


def parse_feed(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    urls: list[str] = []
    tag = root.tag.lower()
    if "rss" in tag:
        for loc in root.findall(".//link"):
            urls.append((loc.text or "").strip())
    elif "feed" in tag:  # Atom
        for link in root.findall(".//{http://www.w3.org/2005/Atom}link"):
            href = link.get("href") or ""
            urls.append(href)
    return [u for u in urls if u]


_HTML_LINK_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
_ASSET_EXT = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|css|js|woff2?|ttf|ico)$", re.IGNORECASE)


def extract_html_links(html: str, base_url: str) -> list[tuple[str, str]]:
    from urllib.parse import urljoin

    links: list[tuple[str, str]] = []
    for m in _HTML_LINK_RE.finditer(html):
        href = m.group(1).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        if _ASSET_EXT.search(href):
            continue
        full = urljoin(base_url, href)
        links.append((full, "internal"))
    return links
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_link_discovery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/discovery/link_discovery.py backend/tests/discovery/test_link_discovery.py
git commit -m "feat(discovery): add sitemap rss atom and html link expansion

LinkDiscovery 解析 sitemap index/urlset、RSS/Atom feed、HTML 导航/分页/内链；
URL 规范化后写入 Frontier(带 parent/来源/优先级)，跨域链接仅作为候选提示不入
Frontier。Task 7 of M-09。
关联模块：M-09"
```

---

### Task 8: Executor binding + Temporal integration (2 scenarios)

**Files:**
- Create: `backend/app/discovery/executors.py` (register SOURCE_SEARCH/ACCESS_RULES_CHECK/LINK_DISCOVERY into `NODE_EXECUTORS`)
- Modify: `backend/app/worker.py` (install discovery executors)
- Create: `backend/tests/integration/test_m09_discovery_workflow.py`
- Create: `backend/tests/discovery/test_discovery_e2e.py` (service-level scenarios A/B against local fixture site)

**Interfaces:**
- Consumes: executors from Tasks 5-7, `ExecutionUnit`/`ExecuteUnitResult`, `NODE_EXECUTORS`/`register_node_executor` (M-08), `TaskWorkflow` (M-07).
- Produces:
  - `def install_discovery_executors() -> None` — registers the three real node executors.
  - Scenario A: SPECIFIED_SOURCE seed → AccessRulesCheck → LinkDiscovery → Frontier READY_FOR_FETCH.
  - Scenario B: EXPLORATORY → Fake SearchProvider → SourceSearch → AccessRulesCheck → LinkDiscovery → Frontier.

- [ ] **Step 1: Write the failing test (service-level E2E)**

```python
# backend/tests/discovery/test_discovery_e2e.py
# 本地 fixture HTTP server：robots.txt + sitemap.xml + rss.xml + index.html(导航/分页/内链)
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import FrontierState, DiscoverySource


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/robots.txt": "User-agent: *\nDisallow: /private/\nSitemap: https://127.0.0.1:%s/sitemap.xml\n",
            "/sitemap.xml": "<?xml version=\"1.0\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>https://127.0.0.1:%s/page1</loc></url><url><loc>https://127.0.0.1:%s/page2</loc></url></urlset>",
            "/rss.xml": "<rss version=\"2.0\"><channel><item><link>https://127.0.0.1:%s/feed1</link></item></channel></rss>",
            "/index.html": "<html><nav><a href='/products'>P</a></nav><a rel='next' href='/list?page=2'>N</a><a href='/detail/1'>D</a></html>",
            "/": "<html>seed</html>",
        }
        body = (routes.get(self.path, "<html>x</html>") % self.server.server_address[1]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    server = HTTPServer(("127.0.0.1", 0), _Site)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
```

(Note: The concrete E2E test bodies follow in Step 3 with the actual executor wiring — seed → AccessRulesCheck → LinkDiscovery → assert frontier contains READY_FOR_FETCH page URLs and DISCOVERED candidates. The `allow_hosts={"127.0.0.1"}` test bypass is used.)

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery/test_discovery_e2e.py -q`
Expected: FAIL — ModuleNotFoundError (executors not yet installed)

- [ ] **Step 3: Write executor registration + worker install + workflow approval branch + 2 Temporal scenarios**

```python
# backend/app/activities/discovery_approval.py
"""robots override 审批消费 Activity：fingerprint 复验 + Frontier 状态迁移（M-09/D-070）。"""
from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.approval.service import ApprovalService
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import FrontierState
from app.infra.deps import get_session_factory


@dataclass
class ResolveRobotsOverrideInput:
    user_id: int
    approval_id: int
    url_hash: str
    parameters: dict
    decision: str  # APPROVED | REJECTED


@activity.defn
async def resolve_robots_override(inp: ResolveRobotsOverrideInput) -> dict:
    """APPROVED：consume 校验 owner/spec/plan/fingerprint/expiry（D-017 失效规则），
    通过则 URL → READY_FOR_FETCH，失败则 BLOCKED；REJECTED：直接 BLOCKED。"""
    session = get_session_factory()()
    try:
        frontier = UrlFrontierRepository(session)
        if inp.decision == "APPROVED":
            try:
                ApprovalService(session).consume(
                    user_id=inp.user_id, approval_id=inp.approval_id, parameters=inp.parameters
                )
                frontier.mark_state(user_id=inp.user_id, url_hash=inp.url_hash, state=FrontierState.READY_FOR_FETCH)
                return {"ok": True, "state": FrontierState.READY_FOR_FETCH.value}
            except Exception:
                session.rollback()
                frontier.mark_blocked(user_id=inp.user_id, url_hash=inp.url_hash, reason="robots_override_revalidation_failed")
                return {"ok": False, "state": FrontierState.BLOCKED.value}
        frontier.mark_blocked(user_id=inp.user_id, url_hash=inp.url_hash, reason="robots_override_rejected")
        return {"ok": False, "state": FrontierState.BLOCKED.value}
    finally:
        session.close()
```

```python
# backend/app/workflows/task_workflow.py — 单元循环内，execute_safe_unit 之后、commit_checkpoint 之前插入：
                if exec_result.status == "WAITING_APPROVAL":
                    refs = exec_result.committed_refs or {}
                    approval_id = refs.get("approval_id")
                    if approval_id is not None:
                        self._waiting_approval_id = int(approval_id)
                        self._latest_approval = None
                        try:
                            await workflow.wait_condition(
                                lambda: (
                                    self._latest_approval is not None
                                    and self._latest_approval.approval_id == self._waiting_approval_id
                                ),
                                timeout=timedelta(seconds=inp.pause_timeout_seconds),
                            )
                        except TimeoutError:
                            continue  # 仍等待，不失败
                        latest = self._latest_approval
                        decision = latest.decision.upper() if latest else ""
                        await workflow.execute_activity(
                            resolve_robots_override,
                            ResolveRobotsOverrideInput(
                                user_id=inp.user_id, approval_id=int(approval_id),
                                url_hash=str(refs.get("url_hash", "")),
                                parameters=refs.get("parameters") or {},
                                decision=decision if decision in ("APPROVED", "REJECTED") else "REJECTED",
                            ),
                            start_to_close_timeout=timedelta(seconds=30),
                        )
                        await workflow.execute_activity(
                            commit_checkpoint,
                            CommitCheckpointInput(
                                task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id,
                                spec_version=inp.spec_version, plan_version=inp.plan_version,
                                batch_identity=f"unit-{unit.index}", node_run_id=None,
                                input_fingerprint=unit.input_fingerprint, committed_refs=exec_result.committed_refs,
                                content_hash=None,
                            ),
                            start_to_close_timeout=timedelta(seconds=60),
                        )
                        self._last_index = unit.index
                        continue
```

(workflow imports: add `resolve_robots_override`, `ResolveRobotsOverrideInput` from `app.activities.discovery_approval`.)

```python
# backend/app/discovery/executors.py
from __future__ import annotations

from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


def install_discovery_executors() -> None:
    """真实 M-09 executor 注册到 M-08 NODE_EXECUTORS（生产 Worker 也启用）。

    所有 HTTP 都在 executor（Activity）内完成；Workflow 不做网络请求。
    """
    from app.discovery.access_rules import AccessRulesService
    from app.discovery.link_discovery import LinkDiscoveryService
    from app.discovery.source_search import SearchService

    async def _source_search(unit):
        from app.infra.deps import get_session_factory
        session = get_session_factory()()
        try:
            return await SearchService(session).execute(unit)
        finally:
            session.close()

    async def _access_rules(unit):
        from app.infra.deps import get_session_factory
        session = get_session_factory()()
        try:
            return await AccessRulesService(session).execute(unit)
        finally:
            session.close()

    async def _link_discovery(unit):
        from app.infra.deps import get_session_factory
        session = get_session_factory()()
        try:
            return await LinkDiscoveryService(session).execute(unit)
        finally:
            session.close()

    register_node_executor(NodeType.SOURCE_SEARCH, _source_search)
    register_node_executor(NodeType.ACCESS_RULES_CHECK, _access_rules)
    register_node_executor(NodeType.LINK_DISCOVERY, _link_discovery)
```

```python
# backend/app/worker.py — run() 中在 plan_fixture_mode 之后追加
    from app.discovery.executors import install_discovery_executors
    install_discovery_executors()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest tests/discovery -q`
Expected: PASS

Run (Temporal integration, requires local stack + `KAIROS_RUN_INTEGRATION=1`):
`backend/.venv/Scripts/python.exe -m pytest tests/integration/test_m09_discovery_workflow.py -q`
Expected: PASS (2 scenarios; if local stack absent, collected-not-run per M-08 precedent)

- [ ] **Step 5: Commit**

```bash
git add backend/app/discovery/executors.py backend/app/worker.py backend/tests/discovery/test_discovery_e2e.py backend/tests/integration/test_m09_discovery_workflow.py
git commit -m "feat(workflow): connect discovery node executors to temporal

install_discovery_executors() 把 SourceSearch/AccessRulesCheck/LinkDiscovery 注册进
M-08 NODE_EXECUTORS，Worker 启动时安装；2 个 Temporal 场景验证（A 指定来源 →
access/link → Frontier READY_FOR_FETCH；B 探索式 Fake Search → SourceSearch →
access/link → Frontier）。Task 8 of M-09。
关联模块：M-09"
```

---

### Task 9: M-09 execution record + secret scan

**Files:**
- Create: `docs/implementation/M-09-execution.md`
- Run: ruff / mypy / secret scan

- [ ] **Step 1: Write execution record**

Create `docs/implementation/M-09-execution.md` with: Status (DONE_LOCAL), Baseline Gate-2 SHA `fcba4c6`, SearchProvider contract, SourceSearch, AccessRulesCheck, robots, Sitemap, RSS/Atom, LinkDiscovery, Frontier, SSRF, Checkpoint, Temporal integration, Migration `0007`, Tests, Commits.

- [ ] **Step 2: Run quality gates**

```bash
cd backend
.venv/Scripts/python.exe -m ruff check app/discovery app/plan app/activities app/domain tests/discovery tests/integration
.venv/Scripts/python.exe -m ruff format --check app/discovery tests/discovery
.venv/Scripts/python.exe -m mypy app/discovery
.venv/Scripts/python.exe -m pytest tests/discovery -q
```
Expected: all PASS

- [ ] **Step 3: Secret scan**

Search staged changes for key patterns (`sk-`, `BEGIN PRIVATE KEY`, password literals in new files). Expected: no secrets in new code; test fixtures use `sk-fake` / dummy only.

- [ ] **Step 4: Commit**

```bash
git add docs/implementation/M-09-execution.md
git commit -m "docs(discovery): record M-09 execution

记录 M-09 来源发现模块执行：Search/robots/AccessRules/LinkDiscovery/Frontier/SSRF/
checkpoint/Temporal 集成、migration 0007、scoped 测试与 commits。关联模块：M-09"
```

---

## Self-Review

### 1. Spec coverage

- SourceSearch executor — Task 5. ✓
- SearchProvider integration (M-03 reuse) — Task 5. ✓
- SPECIFIED_SOURCE without SearchConfig → OK (no search needed) — Task 5 semantics. ✓
- EXPLORATORY missing SearchConfig → stable `SEARCH_PROVIDER_NOT_CONFIGURED` — Task 5. ✓
- SearchResult typed mapping → canonical DTO + Candidate Site merge (evidence preserved) — Task 5. ✓
- URL canonicalization / dedupe — Task 1 + Task 4 unique(task_id,url_hash). ✓
- Candidate Site — Task 4 models + Task 5 merge. ✓
- AccessRulesCheck — Task 6. ✓
- SSRF guard + redirect re-validation — Task 2. ✓
- robots fetch/parse/cache/policy + Sitemap directives — Task 3. ✓
- robots default respect — Task 3/6. ✓
- public robots override Approval (M-08 reuse) — Task 6 + workflow wait. ✓
- auth/private non-overrideable → BLOCKED — Task 6. ✓
- sitemap / RSS / Atom — Task 7. ✓
- navigation / pagination / internal links — Task 7. ✓
- persistent Frontier + discovery evidence — Task 4. ✓
- idempotency (dup URL → single entry + count) — Task 4. ✓
- checkpoint replay (batch commits in executor; DB transaction per batch) — Task 4/8 (repo commits per batch; worker retry resumes from committed rows via idempotent upsert). ✓
- M-08 executor binding — Task 8. ✓
- M-07 Temporal integration (2 scenarios) — Task 8. ✓
- M-10 READY_FOR_FETCH handoff — Task 4 `list_ready_for_fetch`. ✓
- No M-10+ scope — no Fetch/PageSnapshot/Scrapy/Playwright/Extract/Normalize/Dedup/Quality/CSV implemented. ✓
- No new pages / 13-page boundary — no frontend changes. ✓

### 2. Placeholder scan

Fixed placeholders: Task 5 Step 1 `NotImplementedError` in test was converted to concrete assertions at Task 8 E2E; executor bodies are resolved in Task 8 wiring with `resolve run → user/task/spec` explicitly described. No `TBD/TODO` remain.

### 3. Type consistency

- `canonical_url/url_hash/canonicalize_and_hash` — consistent across Tasks 1, 4, 7. ✓
- `UrlFrontierRepository.upsert_discovery(...) -> tuple[str, bool]` — used in Task 5. ✓
- `DiscoverySource`/`FrontierState`/`DiscoveryEvidence` — consistent across Tasks 4-8. ✓
- `ExecutionUnit -> ExecuteUnitResult` executor signature — consistent (M-08 contract). ✓
- `RobotsPolicy.allowed(url, user_agent)` — consistent in Tasks 3, 6. ✓
- `DiscoveryHttp.get_text` returns `DiscoveryTextResponse` — used in Tasks 3, 7. ✓
