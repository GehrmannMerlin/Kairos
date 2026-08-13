"""日志/OTel/备份 manifest 脱敏。复用 M-14/M-16 脱敏语义，统一收口。

命中即把整段值替换为脱敏占位；值本身绝不进入日志、OTel span、备份 manifest。
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(api[_-]?key[=:\s]+\S+)"), r"<redacted:api_key>"),
    (re.compile(r"(?i)(secret[_-]?key[=:\s]+\S+)"), r"<redacted:secret>"),
    (re.compile(r"(?i)(password[=:\s]+\S+)"), r"<redacted:password>"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+\S+)"), r"<redacted:authorization>"),
    (re.compile(r"(?i)(session[_-]?secret[=:\s]+\S+)"), r"<redacted:session_secret>"),
    (re.compile(r"(?i)(credential[_-]?master[_-]?key[=:\s]+\S+)"), r"<redacted:master_key>"),
    (re.compile(r"(postgres(?:ql)?\+psycopg://[^:\s@]+:)[^@\s]+(@)"), r"\1<redacted>\2"),
    (re.compile(r"(?i)(set-cookie:\s*[^=;]+=)[^;]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(cookie:\s*[^=;]+=)[^;]+"), r"\1<redacted>"),
]


def redact_line(line: str) -> str:
    """逐行脱敏：API Key / Authorization / Cookie / password / secret / master key / DB URL 密码。"""  # noqa: E501
    out = line
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """只保留 header 名，值全部脱敏（同 crawling.contracts.redact_headers 语义）。"""
    if not headers:
        return {}
    return dict.fromkeys(headers, "<redacted>")
