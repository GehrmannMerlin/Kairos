"""Deterministic API-key fingerprinting for the model probe.

Security contract (D-073): one probe may send the key to at most ONE external
provider. Fingerprinting is pure local string matching — it never makes a
network call and never sends the key anywhere. Only high-confidence prefixes are
used; generic ``sk-`` keys (shared by OpenAI/DeepSeek) are intentionally
AMBIGUOUS so we never broadcast one vendor's key to another.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.protocol import DetectionConfidence

# Ordered most-specific-first (prefix, provider_type) high-confidence pairs.
# ``sk-ant-`` / ``sk-or-`` / ``sk-proj-`` must be checked before the generic
# ``sk-`` catch-all below.
_HIGH_CONFIDENCE: tuple[tuple[str, str], ...] = (
    ("sk-ant-", "anthropic"),
    ("sk-or-", "openrouter"),
    ("sk-proj-", "openai"),
    ("sk-svcacct-", "openai"),
)

# Generic ``sk-`` is shared by several vendors — never guess.
_AMBIGUOUS_SK_CANDIDATES: tuple[str, ...] = ("openai", "deepseek")

# Google API keys.
_GOOGLE_PREFIX = "AIza"


@dataclass(frozen=True)
class KeyFingerprint:
    confidence: DetectionConfidence
    provider_type: str | None
    candidates: tuple[str, ...] = ()


def fingerprint_api_key(api_key: str) -> KeyFingerprint:
    key = (api_key or "").strip()
    if not key:
        return KeyFingerprint(DetectionConfidence.NONE, None)
    for prefix, provider_type in _HIGH_CONFIDENCE:
        if key.startswith(prefix):
            return KeyFingerprint(DetectionConfidence.HIGH, provider_type, (provider_type,))
    if key.startswith(_GOOGLE_PREFIX):
        return KeyFingerprint(DetectionConfidence.HIGH, "gemini", ("gemini",))
    if key.startswith("sk-"):
        return KeyFingerprint(DetectionConfidence.AMBIGUOUS, None, _AMBIGUOUS_SK_CANDIDATES)
    return KeyFingerprint(DetectionConfidence.NONE, None)
