"""SSRF guard for discovery HTTP (M-09 / D-070 安全边界).

拒绝：localhost、127.0.0.0/8、::1、link-local、169.254.0.0/16、RFC1918 私网、
云 metadata（169.254.169.254）、file:// ftp:// 等非 http(s) scheme。
字面 IP 与 DNS 解析后 IP 都必须为公网。本地测试 fixture 通过显式 allow_hosts
绕过；Production 默认关闭绕过（allow_hosts 为空）。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.discovery.errors import DiscoveryError


class SSRFBlockedError(DiscoveryError):
    pass


def _split(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFBlockedError(f"禁止的 scheme: {parsed.scheme or '(空)'}")
    host = parsed.hostname
    if not host:
        raise SSRFBlockedError("URL 缺少主机名")
    port = parsed.port or (80 if parsed.scheme.lower() == "http" else 443)
    return parsed.scheme.lower(), host, port


def _host_allowed(host: str, allow_hosts: frozenset[str]) -> bool:
    if host in allow_hosts:
        return True
    try:
        return str(ipaddress.ip_address(host)) in allow_hosts
    except ValueError:
        return False


_IpLike = str | ipaddress.IPv4Address | ipaddress.IPv6Address


def _check_ip(ip: _IpLike) -> None:
    addr = (
        ip
        if isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address))
        else ipaddress.ip_address(ip)
    )
    if not addr.is_global:
        raise SSRFBlockedError(f"目标解析到非公网地址: {addr}")


def assert_safe_url(url: str, *, allow_hosts: frozenset[str] = frozenset()) -> None:
    """SSRF 守卫：字面/解析后 IP 都必须为公网；测试用显式 allow_hosts 绕过。"""
    scheme, host, port = _split(url)
    if _host_allowed(host, allow_hosts):
        return
    # 字面 IP 直接判定（公网 IP 字面无需 DNS）
    try:
        ip = ipaddress.ip_address(host)
        _check_ip(ip)
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
    seen: set[str] = set()
    for info in infos:
        ip_str = str(info[4][0])
        if ip_str in seen:
            continue
        seen.add(ip_str)
        _check_ip(ip_str)
