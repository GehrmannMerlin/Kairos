"""ModelInferenceClient — typed LLM text generation through M-03 (M-06).

One inference path for every registered ModelProvider family, driven by the same
M-03 ``HttpClient`` transport (no second SDK). The client receives an already
decrypted ``api_key`` (from CredentialVault, execution-time only) plus a frozen
``ResolvedModel``; it never sees a secret and never logs one.

Errors map to the M-03 taxonomy (AUTH_FAILED / MODEL_NOT_FOUND / RATE_LIMITED /
NETWORK_ERROR) so the frontend Global Error Mapper can present a recoverable
Chat error instead of a bare 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from app.providers import errors
from app.providers.protocol import ResolvedModel
from app.providers.transport import HttpClient, HttpxTransport

ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class InferenceResult:
    text: str
    provider_type: str
    duration_ms: int


def _map_http_error(http_status: int) -> errors.ProviderError:
    if http_status in (401, 403):
        return errors.ProviderAuthFailedError(f"HTTP_{http_status}")
    if http_status == 404:
        return errors.ProviderModelNotFoundError("HTTP_404")
    if http_status == 429:
        return errors.ProviderRateLimitedError("HTTP_429")
    return errors.ProviderNetworkError(f"HTTP_{http_status}")


class ModelInferenceClient:
    def __init__(
        self, http: HttpClient | None = None, timeout_seconds: float | None = None
    ) -> None:
        self._http = http or HttpxTransport()
        self._timeout = timeout_seconds or _DEFAULT_TIMEOUT_SECONDS

    async def generate(
        self,
        *,
        resolved: ResolvedModel,
        api_key: str | None,
        system: str,
        user: str,
    ) -> InferenceResult:
        started = perf_counter()
        family = resolved.provider_type
        try:
            if family in ("openai", "deepseek", "openrouter", "custom_openai_compatible", "ollama"):
                text = await self._openai_compatible(resolved, api_key, system, user)
            elif family == "anthropic":
                text = await self._anthropic(resolved, api_key, system, user)
            elif family == "gemini":
                text = await self._gemini(resolved, api_key, system, user)
            else:
                raise errors.ProviderInferenceError(f"不支持的推理 Provider: {family}")
        except errors.ProviderError:
            raise
        except Exception as exc:
            raise errors.ProviderNetworkError("推理请求失败") from exc
        if not text:
            raise errors.ProviderInferenceError("模型未返回可用内容")
        return InferenceResult(
            text=text, provider_type=family, duration_ms=int((perf_counter() - started) * 1000)
        )

    # ---- wire formats ----

    async def _openai_compatible(
        self, resolved: ResolvedModel, api_key: str | None, system: str, user: str
    ) -> str:
        base = (resolved.base_url or "").rstrip("/")
        headers = {"Content-Type": "application/json"}
        if resolved.provider_type != "ollama" and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": resolved.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        resp = await self._http.request(
            method="POST",
            url=f"{base}/chat/completions",
            headers=headers,
            params=None,
            timeout_seconds=self._timeout,
            body=body,
        )
        if resp.status_code != 200:
            raise _map_http_error(resp.status_code)
        choices = (resp.body or {}).get("choices") or []
        if not choices:
            raise errors.ProviderInferenceError("响应缺少 choices")
        content = (choices[0].get("message") or {}).get("content")
        return content or ""

    async def _anthropic(
        self, resolved: ResolvedModel, api_key: str | None, system: str, user: str
    ) -> str:
        base = (resolved.base_url or "").rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        body = {
            "model": resolved.model_name,
            "max_tokens": 2048,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        resp = await self._http.request(
            method="POST",
            url=f"{base}/v1/messages",
            headers=headers,
            params=None,
            timeout_seconds=self._timeout,
            body=body,
        )
        if resp.status_code != 200:
            raise _map_http_error(resp.status_code)
        content = (resp.body or {}).get("content") or []
        if not content:
            raise errors.ProviderInferenceError("响应缺少 content")
        return content[0].get("text") or ""

    async def _gemini(
        self, resolved: ResolvedModel, api_key: str | None, system: str, user: str
    ) -> str:
        base = (resolved.base_url or "").rstrip("/")
        headers: dict[str, str] = {}
        params = {"key": api_key or ""} if api_key else None
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system}\n\n{user}"}],
                }
            ]
        }
        resp = await self._http.request(
            method="POST",
            url=f"{base}/models/{resolved.model_name}:generateContent",
            headers=headers,
            params=params,
            timeout_seconds=self._timeout,
            body=body,
        )
        if resp.status_code != 200:
            raise _map_http_error(resp.status_code)
        candidates = (resp.body or {}).get("candidates") or []
        if not candidates:
            raise errors.ProviderInferenceError("响应缺少 candidates")
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        return parts[0].get("text") or ""
