"""Dynamic content classification regression tests (M-10 / DEPLOY-GATE-3).

Task 143 root cause: a real JavaScript application shell whose only visible
text comes from a <noscript> fallback ("请启用 JavaScript 后继续访问") was
classified HTML_STATIC instead of DYNAMIC_SHELL, so Playwright escalation
never fired. noscript fallback text is NOT page content and must not make a
JS shell look like a static page — but a real static body must stay static.
"""
from __future__ import annotations

from app.crawling.content import (
    ContentClass,
    classify_content,
)

URL = "https://example.com/page"


def _shell_with_noscript() -> bytes:
    return (
        "<html><head><script src='/app.js'></script></head>"
        "<body><div id='app'></div>"
        "<noscript>请启用 JavaScript 后继续访问本站</noscript>"
        "<script>window.__APP_BOOTSTRAP__ = {};</script>"
        "</body></html>"
    ).encode()


def _static_article() -> bytes:
    return (
        "<html><body><article>"
        "上海市发布人工智能产业发展相关政策，推动大模型应用落地。"
        "</article></body></html>"
    ).encode()


def _static_article_with_noscript() -> bytes:
    return (
        "<html><body><article>"
        "上海市发布人工智能产业发展相关政策，推动大模型应用落地。"
        "</article><noscript>部分功能需要启用 JavaScript</noscript>"
        "</body></html>"
    ).encode()


class TestDynamicClassification:
    def test_js_shell_with_noscript_is_dynamic(self) -> None:
        """Case A: app shell + scripts + noscript fallback, no real content -> DYNAMIC_SHELL."""
        result = classify_content(
            url=URL, content_type="text/html", body=_shell_with_noscript()
        )
        assert result == ContentClass.DYNAMIC_SHELL

    def test_static_article_stays_static(self) -> None:
        """Case B: real article text -> HTML_STATIC (no regression on normal pages)."""
        result = classify_content(
            url=URL, content_type="text/html", body=_static_article()
        )
        assert result == ContentClass.HTML_STATIC

    def test_static_article_with_noscript_stays_static(self) -> None:
        """Case C: real body + noscript -> still HTML_STATIC (noscript alone is not dynamic)."""
        result = classify_content(
            url=URL, content_type="text/html", body=_static_article_with_noscript()
        )
        assert result == ContentClass.HTML_STATIC
