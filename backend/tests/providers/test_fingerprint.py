"""API-key fingerprinting (deterministic, local, never sends the key)."""

from __future__ import annotations

from app.providers.fingerprint import fingerprint_api_key
from app.providers.protocol import DetectionConfidence


def test_high_confidence_anthropic() -> None:
    fp = fingerprint_api_key("sk-ant-api03-abcdef")
    assert fp.confidence is DetectionConfidence.HIGH
    assert fp.provider_type == "anthropic"
    assert fp.candidates == ("anthropic",)


def test_high_confidence_openrouter() -> None:
    fp = fingerprint_api_key("sk-or-v1-abcdef")
    assert fp.confidence is DetectionConfidence.HIGH
    assert fp.provider_type == "openrouter"


def test_high_confidence_openai_project() -> None:
    fp = fingerprint_api_key("sk-proj-abcdef")
    assert fp.confidence is DetectionConfidence.HIGH
    assert fp.provider_type == "openai"


def test_high_confidence_gemini() -> None:
    fp = fingerprint_api_key("AIzaSyABCDEFG")
    assert fp.confidence is DetectionConfidence.HIGH
    assert fp.provider_type == "gemini"


def test_ambiguous_generic_sk() -> None:
    # OpenAI and DeepSeek both use generic sk-* keys: must not guess.
    fp = fingerprint_api_key("sk-1234567890abcdef")
    assert fp.confidence is DetectionConfidence.AMBIGUOUS
    assert fp.provider_type is None
    assert set(fp.candidates) == {"openai", "deepseek"}


def test_none_for_unknown_or_empty() -> None:
    assert fingerprint_api_key("").confidence is DetectionConfidence.NONE
    assert fingerprint_api_key("   ").confidence is DetectionConfidence.NONE
    assert fingerprint_api_key("totally-unknown-format").confidence is DetectionConfidence.NONE
