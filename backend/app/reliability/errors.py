"""M-16 统一错误分类（ErrorClass）。

不是第二套错误框架：它把既有 taxonomy（FetchErrorCode / ProviderError /
HTTP status）映射到一个可靠性视图，供 retry decision / circuit breaker /
provider limiter 共享。crawling/errors.py 明令不造两套，这里只做映射层。
"""

from __future__ import annotations

from enum import StrEnum

from app.crawling.errors import FetchErrorCode
from app.providers import errors as provider_errors


class ErrorClass(StrEnum):
    NETWORK_TIMEOUT = "network_timeout"
    TRANSIENT_SERVICE_ERROR = "transient_service_error"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    QUOTA_EXHAUSTED = "quota_exhausted"
    STRUCTURE_CHANGED = "structure_changed"
    EXTRACTION_FAILED = "extraction_failed"
    QUALITY_FAILED = "quality_failed"
    DOMAIN_UNAVAILABLE = "domain_unavailable"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    CANCELLED = "cancelled"
    NON_RETRYABLE = "non_retryable"


def classify_http_error(http_status: int, *, retry_after: float | None = None) -> ErrorClass:
    """确定性 HTTP 状态码分类。429 → RATE_LIMITED（Retry-After 单独携带）。"""
    del retry_after  # 只影响 delay，不影响 class
    if http_status in (408, 425):
        return ErrorClass.NETWORK_TIMEOUT
    if http_status == 429:
        return ErrorClass.RATE_LIMITED
    if http_status in (401, 403):
        return ErrorClass.AUTH_FAILED
    if 500 <= http_status < 600:
        return ErrorClass.TRANSIENT_SERVICE_ERROR
    return ErrorClass.NON_RETRYABLE


# FetchErrorCode（crawling/errors.py）→ ErrorClass 映射。retry 分类与 breaker 计数分离：
# is_domain_breaker_error 单独判断哪些 class 代表「目标域名/服务不可用」。
_FETCH_CODE_MAP: dict[FetchErrorCode, ErrorClass] = {
    FetchErrorCode.TIMEOUT: ErrorClass.NETWORK_TIMEOUT,
    FetchErrorCode.DNS_ERROR: ErrorClass.NETWORK_TIMEOUT,
    FetchErrorCode.CONNECTION_ERROR: ErrorClass.NETWORK_TIMEOUT,
    FetchErrorCode.SERVER_ERROR: ErrorClass.TRANSIENT_SERVICE_ERROR,
    FetchErrorCode.RATE_LIMITED: ErrorClass.RATE_LIMITED,
    FetchErrorCode.AUTH_REQUIRED: ErrorClass.AUTH_FAILED,
    FetchErrorCode.ACCESS_DENIED: ErrorClass.AUTH_FAILED,
    FetchErrorCode.CAPTCHA_REQUIRED: ErrorClass.AUTH_FAILED,  # 需人工处理，非域名崩溃
    FetchErrorCode.CREDENTIAL_REQUIRED: ErrorClass.AUTH_FAILED,  # 需凭据，非域名崩溃
    FetchErrorCode.NOT_FOUND: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.TOO_MANY_REDIRECTS: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.SIZE_LIMIT_EXCEEDED: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.SSRF_BLOCKED: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.STORAGE_ERROR: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.INTERNAL_ERROR: ErrorClass.NON_RETRYABLE,
    FetchErrorCode.EMPTY_CONTENT: ErrorClass.STRUCTURE_CHANGED,  # 策略需升级/更换
    FetchErrorCode.DYNAMIC_RENDER_REQUIRED: ErrorClass.STRUCTURE_CHANGED,  # 需升级工具
    FetchErrorCode.UNSUPPORTED_RESPONSE: ErrorClass.STRUCTURE_CHANGED,
}


def classify_fetch_error_code(code: FetchErrorCode) -> ErrorClass:
    return _FETCH_CODE_MAP.get(code, ErrorClass.NON_RETRYABLE)


def classify_provider_error(exc: Exception) -> ErrorClass:
    if isinstance(exc, provider_errors.ProviderAuthFailedError):
        return ErrorClass.AUTH_FAILED
    if isinstance(exc, provider_errors.ProviderRateLimitedError):
        return ErrorClass.RATE_LIMITED
    if isinstance(exc, provider_errors.ProviderNetworkError):
        return ErrorClass.NETWORK_TIMEOUT
    if isinstance(exc, provider_errors.ProviderInferenceError):
        # 调用成功但无可用输出 → 语义失败，需纠错变化而非盲目重试（D-013 §13）
        return ErrorClass.EXTRACTION_FAILED
    if isinstance(exc, provider_errors.ProviderModelNotFoundError):
        return ErrorClass.NON_RETRYABLE
    if isinstance(
        exc,
        (
            provider_errors.ModelNotConfiguredError,
            provider_errors.SearchProviderNotConfiguredError,
        ),
    ):
        return ErrorClass.NON_RETRYABLE
    if isinstance(exc, provider_errors.ProviderError):
        return ErrorClass.NON_RETRYABLE
    return ErrorClass.NON_RETRYABLE


_DOMAIN_BREAKER_CLASSES = frozenset(
    {ErrorClass.NETWORK_TIMEOUT, ErrorClass.TRANSIENT_SERVICE_ERROR, ErrorClass.DOMAIN_UNAVAILABLE}
)


def is_domain_breaker_error(error_class: ErrorClass) -> bool:
    """只有「目标域名/服务不可用」类错误计入 Domain Breaker（D-013 §20）。"""
    return error_class in _DOMAIN_BREAKER_CLASSES
