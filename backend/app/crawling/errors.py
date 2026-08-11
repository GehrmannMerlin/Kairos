"""M-10 crawling 错误分类（复用 Code Standards 10.3 taxonomy 子集）。

Fetch 至少稳定区分 TIMEOUT/DNS_ERROR/CONNECTION_ERROR/RATE_LIMITED/SERVER_ERROR/
NOT_FOUND/AUTH_REQUIRED/ACCESS_DENIED/CAPTCHA_REQUIRED/UNSUPPORTED_RESPONSE/
EMPTY_CONTENT/DYNAMIC_RENDER_REQUIRED/CREDENTIAL_REQUIRED/STORAGE_ERROR。
实际 canonical names 以本模块为准，M-11/M-12 复用同一 enum，不造两套。
"""

from __future__ import annotations

from enum import StrEnum


class FetchErrorCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    DNS_ERROR = "DNS_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    NOT_FOUND = "NOT_FOUND"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    UNSUPPORTED_RESPONSE = "UNSUPPORTED_RESPONSE"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    DYNAMIC_RENDER_REQUIRED = "DYNAMIC_RENDER_REQUIRED"
    CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"
    STORAGE_ERROR = "STORAGE_ERROR"
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"
    SIZE_LIMIT_EXCEEDED = "SIZE_LIMIT_EXCEEDED"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 可内部有界重试：网络瞬时/5xx/429。401/403/404/captcha 等不在此集合（不重试、不升级）。
RETRYABLE_CODES: frozenset[FetchErrorCode] = frozenset(
    {
        FetchErrorCode.TIMEOUT,
        FetchErrorCode.DNS_ERROR,
        FetchErrorCode.CONNECTION_ERROR,
        FetchErrorCode.RATE_LIMITED,
        FetchErrorCode.SERVER_ERROR,
    }
)


class CrawlingError(Exception):
    """M-10 crawling 错误基类（分类到 M-03 错误分类体系）。"""


class SnapshotCommitError(CrawlingError):
    """PageSnapshot 对象/DB 提交失败（业务上 Fetch 未成功提交）。"""


class BrowserRenderError(CrawlingError):
    """Playwright 渲染失败（不无限 Browser retry）。"""
