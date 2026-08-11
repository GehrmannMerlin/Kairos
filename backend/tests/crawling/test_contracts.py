"""M-10 契约测试：FetchRequest 禁止 Secret 字段；错误分类有界重试映射。"""
from __future__ import annotations

from app.crawling.contracts import FetchRequest
from app.crawling.errors import (
    RETRYABLE_CODES,
    FetchErrorCode,
)


def test_fetch_request_forbids_secret_fields() -> None:
    fields = set(FetchRequest.model_fields)
    assert not ({"cookie", "password", "authorization", "set_cookie"} & fields)
    assert "credential_ref" in fields  # 只允许脱敏引用


def test_fetch_request_rejects_extra_keys() -> None:
    # typed contract 严格：未知键拒绝（D-008）
    import pydantic

    try:
        FetchRequest(
            task_id=1, user_id=1, run_id=1, spec_version=1, canonical_url="https://a.com/",
            url_hash="h", cookie="secret"
        )
    except pydantic.ValidationError:
        return
    raise AssertionError("FetchRequest 应拒绝未声明的 cookie 字段")


def test_fetch_error_code_retryable_map() -> None:
    assert {FetchErrorCode.SERVER_ERROR, FetchErrorCode.RATE_LIMITED} <= RETRYABLE_CODES
    assert FetchErrorCode.NOT_FOUND not in RETRYABLE_CODES
    assert FetchErrorCode.AUTH_REQUIRED not in RETRYABLE_CODES
    assert FetchErrorCode.CAPTCHA_REQUIRED not in RETRYABLE_CODES
    assert FetchErrorCode.ACCESS_DENIED not in RETRYABLE_CODES
