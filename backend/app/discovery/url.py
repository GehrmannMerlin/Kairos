"""Deterministic URL canonicalization + stable identity (M-09 / D-016).

规范化规则保守且确定性：scheme 小写、hostname 小写 + IDN 安全、默认端口移除、
fragment 移除、dot-segment 归一。query 默认完整保留（tracking denylist 不在
M-09 范围）。禁止非 http(s) scheme 与 URL 内嵌用户信息。
"""

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
    # fragment 移除；query 默认完整保留
    return urlunsplit((scheme, netloc, normalized, parsed.query, ""))


def url_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize_and_hash(raw: str) -> tuple[str, str]:
    c = canonical_url(raw)
    return c, url_hash(c)
