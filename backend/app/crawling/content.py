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

# 检测明确 captcha/challenge marker → CAPTCHA_REQUIRED
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


def _visible_text(body: bytes) -> str:
    """去掉 script/style 与所有标签后的可读文本（attribute 值不算文本节点）。"""
    import re

    text = body.decode("utf-8", errors="ignore")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_substantive_text(body: bytes) -> bool:
    """存在 ≥2 个“词”才视为有实质内容（避免 id/class 属性值误判）。"""
    import re

    words = re.findall(r"[a-zA-Z0-9一-鿿]{2,}", _visible_text(body))
    return len(words) >= 2


_TEXT_MARKUP = r"<p\b|<h[1-6]\b|<li\b|<td\b|<article\b|<table\b"


def _has_text_markup(body: bytes) -> bool:
    """剥离 script/style 后仍存在真实文本标记（<p>/<li>/<td> 等）→ 静态内容。"""
    import re

    text = re.sub(
        r"<script.*?</script>|<style.*?</style>", " ", body.decode("utf-8", errors="ignore"),
        flags=re.S | re.I,
    )
    return re.search(_TEXT_MARKUP, text.lower()) is not None


def classify_content(*, url: str, content_type: str | None, body: bytes) -> ContentClass:
    if _looks_structured(url, content_type):
        return ContentClass.STRUCTURED
    if not body:
        return ContentClass.EMPTY
    # 空壳：无文本标记、无可读正文 → 可能需浏览器渲染（DYNAMIC_APP_SHELL）
    if not _has_text_markup(body) and not _has_substantive_text(body):
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
