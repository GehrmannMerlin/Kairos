"""内容分类与升级证据（M-10 / 十九 / 二十）。

结构化（JSON/XML/RSS/Atom/Sitemap）→ 静态 HTML → 空/JS shell（触发证据升级）。
升级必须有证据；401/403/captcha 不是升级理由，由 executor 按 auth/access 处理。
"""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit

from app.crawling.contracts import EscalationEvidence, EscalationKind

_STRUCTURED_SUFFIXES = (".json", ".xml", ".rss", ".atom")
_STRUCTURED_TYPES = (
    "application/json",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "text/xml",
)

# 明显动态壳特征：正文只有这些 + script，无实质文本内容
_APP_SHELL_MARKERS = (
    '<div id="app"',
    'id="root"',
    "vue",
    "react",
    "nuxt",
    "next data",
    "window.__",
)
_CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "cf-challenge",
    "验证码",
)


class ContentClass(StrEnum):
    STRUCTURED = "STRUCTURED"
    HTML_STATIC = "HTML_STATIC"
    EMPTY = "EMPTY"
    DYNAMIC_SHELL = "DYNAMIC_SHELL"


def _looks_structured(url: str, content_type: str | None) -> bool:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _STRUCTURED_TYPES:
            return True
    path = (urlsplit(url).path or "").lower()
    return path.endswith(_STRUCTURED_SUFFIXES)


def _text_present(body: bytes) -> bool:
    """粗略判断是否存在实质 HTML 文本内容（忽略 script/style）。"""
    import re

    text = body.decode("utf-8", errors="ignore")
    lowered = text.lower()
    if len(text.strip()) == 0:
        return False
    # 去掉 script/style 块后是否还有可读文本
    stripped = re.sub(r"<script.*?</script>|<style.*?</style>", "", lowered, flags=re.S)
    return bool(re.search(r"[a-z一-鿿]{2,}", stripped))


def classify_content(*, url: str, content_type: str | None, body: bytes) -> ContentClass:
    import re

    if _looks_structured(url, content_type):
        return ContentClass.STRUCTURED
    if not _text_present(body):
        # 空 body 或纯 JS shell（无实质内容）→ 可能需浏览器渲染
        return ContentClass.DYNAMIC_SHELL if body else ContentClass.EMPTY
    lowered = body.decode("utf-8", errors="ignore").lower()
    # 只有 app shell marker 且几乎无文本 → 动态壳（JS 渲染后才有真实内容）
    if any(m in lowered for m in _APP_SHELL_MARKERS) and not re.search(
        r"<p\b|<h[1-6]\b|<article\b|<li\b|<table\b|<td\b", lowered
    ):
        return ContentClass.DYNAMIC_SHELL
    return ContentClass.HTML_STATIC


def build_escalation_evidence(content_class: ContentClass) -> EscalationEvidence | None:
    """升级证据：EMPTY_BODY / DYNAMIC_APP_SHELL（本轮动态 fixture 主要使用）。"""
    if content_class == ContentClass.EMPTY:
        return EscalationEvidence(kind=EscalationKind.EMPTY_BODY, trigger_tool="http")
    if content_class == ContentClass.DYNAMIC_SHELL:
        return EscalationEvidence(kind=EscalationKind.DYNAMIC_APP_SHELL, trigger_tool="http")
    return None


def contains_captcha(body: bytes) -> bool:
    """检测明确 captcha/challenge marker → CAPTCHA_REQUIRED（三十二）。"""
    if not body:
        return False
    lowered = body.decode("utf-8", errors="ignore").lower()
    return any(m in lowered for m in _CAPTCHA_MARKERS)
