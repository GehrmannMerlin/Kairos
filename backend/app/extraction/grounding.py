"""LLM evidence grounding (三十：幻觉证据不得进入有效结果)。"""

from __future__ import annotations

import unicodedata


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).strip().split()).lower()


def evidence_is_grounded(quote: str, text: str) -> bool:
    q = _norm(quote)
    t = _norm(text)
    return bool(q) and q in t
