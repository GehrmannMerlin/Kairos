from __future__ import annotations

import html
import re
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field
from temporalio.common import RetryPolicy

from app.config import get_settings


class HttpToolInputError(ValueError):
    """A caller/model error that must not be retried by Temporal."""


class TransientHttpError(RuntimeError):
    """A transport or server response that is reasonable to retry."""


class FetchUrlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    status_code: int
    content_type: str
    title: str | None
    text_preview: str = Field(max_length=4000)
    bytes_read: int


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.parts.append(data)


def _text_preview(body: bytes, content_type: str) -> tuple[str, str | None]:
    encoding = "utf-8"
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError:
        text = body.decode(errors="replace")
    if "html" in content_type.lower():
        parser = _TitleParser()
        parser.feed(text)
        title = " ".join(" ".join(parser.parts).split()) or None
        text = re.sub(r"<script\b[^>]*>.*?</script\s*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style\b[^>]*>.*?</style\s*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        return " ".join(html.unescape(text).split())[:4000], title
    return " ".join(text.split())[:4000], None


async def fetch_url(url: str) -> FetchUrlResult:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HttpToolInputError("url must be an absolute http or https URL")

    settings = get_settings()
    body = bytearray()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": "Kairos/0.1 durable-agent"},
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise TransientHttpError(f"transient HTTP status {response.status_code}")
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > settings.http_max_bytes:
                        raise HttpToolInputError("response body exceeds the configured bounded HTTP limit")
                content_type = response.headers.get("content-type", "application/octet-stream")
                text_preview, title = _text_preview(bytes(body), content_type)
                return FetchUrlResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    content_type=content_type.split(";", 1)[0].strip(),
                    title=title,
                    text_preview=text_preview,
                    bytes_read=len(body),
                )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientHttpError(f"HTTP transport failed: {type(exc).__name__}") from exc


WEB_TOOL_ACTIVITY_CONFIG = {
    "start_to_close_timeout": timedelta(seconds=45),
    "retry_policy": RetryPolicy(
        maximum_attempts=3,
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=10),
        non_retryable_error_types=[HttpToolInputError.__name__],
    ),
}
