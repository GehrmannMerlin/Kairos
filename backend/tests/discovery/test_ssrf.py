"""M-09 Task 2: SSRF guard — reject dangerous targets, allow public, test bypass."""

from __future__ import annotations

import pytest
from app.discovery.ssrf import SSRFBlockedError, assert_safe_url


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
def test_assert_safe_url_rejects_dangerous_targets(url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        assert_safe_url(url)


def test_assert_safe_url_allows_public_literal_ip() -> None:
    # 公网 IP 字面，无需 DNS，确定性通过
    assert_safe_url("https://8.8.8.8/x")


def test_explicit_test_bypass_allows_localhost() -> None:
    assert_safe_url("http://127.0.0.1:8000/x", allow_hosts=frozenset({"127.0.0.1"}))
