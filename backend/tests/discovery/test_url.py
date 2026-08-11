"""M-09 Task 1: deterministic URL canonicalizer + stable identity."""

from __future__ import annotations

import pytest
from app.discovery.errors import DiscoveryValidationError
from app.discovery.url import canonical_url, canonicalize_and_hash, url_hash


def test_canonical_fragment_and_default_port_removed() -> None:
    assert canonical_url("https://Example.com:443/path#frag") == "https://example.com/path"
    assert canonical_url("http://example.com:80/a/b") == "http://example.com/a/b"


def test_canonical_dot_segments_and_host_lowercase() -> None:
    assert canonical_url("https://example.com/a/../b/./c") == "https://example.com/b/c"


def test_canonical_preserves_query() -> None:
    assert canonical_url("https://example.com/x?a=1&b=2#top") == "https://example.com/x?a=1&b=2"


def test_canonical_idn_host() -> None:
    assert canonical_url("https://xn--bcher-kva.example/") == "https://xn--bcher-kva.example/"


def test_canonical_rejects_unsupported_scheme_and_userinfo() -> None:
    with pytest.raises(DiscoveryValidationError):
        canonical_url("file:///etc/passwd")
    with pytest.raises(DiscoveryValidationError):
        canonical_url("ftp://example.com/x")
    with pytest.raises(DiscoveryValidationError):
        canonical_url("https://user:pass@example.com/")


def test_hash_is_stable_and_equivalence_dedupes() -> None:
    a = canonicalize_and_hash("https://Example.com:443/x#f")
    b = canonicalize_and_hash("https://example.com/x")
    assert a == b
    assert url_hash("https://example.com/x") == url_hash("https://example.com/x")
    assert len(a[1]) == 64
