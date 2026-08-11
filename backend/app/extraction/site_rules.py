"""SiteRuleExtractor — 只使用已验证 ACTIVE ExtractorRuleVersion（D-010 / 十八）。

禁止可执行任意 rule：selector 只能是 CSS 或 XPath，transform 只能从注册表取，
绝不 eval(rule.transform)（十九）。RULE_MISMATCH 时记录失败并回退下一层（LLM fallback），
第一次失败不永久删除 Rule（二十四）。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from parsel import Selector

from app.domain.models import ExtractorRuleVersion
from app.extraction.confidence import final_confidence
from app.extraction.contracts import (
    ExtractionCandidate,
    ExtractionIssue,
    ExtractionResult,
    ExtractionSettings,
    ExtractorMethod,
)
from app.extraction.normalize import normalize_text, normalize_value
from app.extraction.protocol import ExtractionContext
from app.extraction.repository import ExtractorRuleRepository

# 注册的 deterministic value transforms（安全白名单，禁止 eval/任意代码）。
RULE_TRANSFORMS: dict[str, Any] = {
    "identity": lambda s: s.strip(),
    "strip": lambda s: " ".join(s.strip().split()),
    "lower": lambda s: s.strip().lower(),
    "upper": lambda s: s.strip().upper(),
    "digits": lambda s: "".join(ch for ch in s if ch.isdigit()),
}


def apply_value_transform(value: str, transform: str) -> str:
    fn = RULE_TRANSFORMS.get(transform or "identity", RULE_TRANSFORMS["identity"])
    return fn(value)


def text_value(raw: str) -> str:
    """确定性提取文本内容：selector 命中元素（HTML）时去掉标签，文本/attr 原样返回。"""
    if "<" not in raw:
        return raw
    return " ".join(Selector(text=raw).xpath("//text()").getall()).strip()


class SiteRuleExtractor:
    name = "site_rule"
    version = "1.0.0"

    def __init__(self, db: Any, settings: ExtractionSettings | None = None) -> None:
        self._db = db
        self._settings = settings or ExtractionSettings()
        self._repo = ExtractorRuleRepository(db)

    async def extract(self, ctx: ExtractionContext, *, unresolved: list[str]) -> ExtractionResult:
        started = perf_counter()
        candidates: list[ExtractionCandidate] = []
        issues: list[ExtractionIssue] = []
        remaining = list(unresolved)
        site_host = (
            urlsplit(ctx.snapshot_ref.final_url or ctx.snapshot_ref.url).hostname or ""
        ).lower()
        active_rules = self._repo.active_for_fields(
            user_id=ctx.user_id or 0, site_host=site_host, field_names=remaining
        )
        rule_by_field: dict[str, ExtractorRuleVersion] = {}
        for active_rule in active_rules:
            rule_by_field.setdefault(active_rule.field_name, active_rule)
        sel = Selector(text=ctx.html)
        for field in ctx.fields:
            if field.name not in remaining:
                continue
            rule = rule_by_field.get(field.name)
            if rule is None:
                continue
            try:
                parts = (
                    sel.css(rule.selector).getall()
                    if rule.rule_type == "css"
                    else sel.xpath(rule.selector).getall()
                )
            except Exception:
                parts = []
            if not parts:
                # RULE_MISMATCH：结构变化或选择器失效 → 失败计数 + 交给下一层（LLM fallback）
                self._repo.increment_failure(rule)
                issues.append(
                    ExtractionIssue(
                        code="RULE_MISMATCH",
                        field_name=field.name,
                        detail=f"rule v{rule.version} selector 无匹配",
                        method=ExtractorMethod.RULE,
                    )
                )
                continue
            raw_value = normalize_text(
                apply_value_transform(text_value(parts[0]), rule.value_transform)
            )
            if not raw_value:
                continue
            normalized = normalize_value(raw_value, field.type)
            locator = f"{rule.rule_type}:{rule.selector}"
            candidates.append(
                ExtractionCandidate(
                    field_name=field.name,
                    raw_value=raw_value,
                    normalized_value=normalized,
                    value_type=field.type.value,
                    method=ExtractorMethod.RULE,
                    confidence=final_confidence(
                        ExtractorMethod.RULE, schema_valid=normalized is not None
                    ),
                    extractor_version=self.version,
                    rule_version=rule.version,
                    source_locator=locator,
                    raw_snippet=raw_value[: self._settings.max_snippet_chars],
                )
            )
            remaining.remove(field.name)
        return ExtractionResult(
            snapshot_id=ctx.snapshot_ref.snapshot_id,
            schema_version=self._settings.schema_version,
            extractor_type=self.name,
            extractor_version=self.version,
            candidates=candidates,
            unresolved_fields=remaining,
            issues=issues,
            duration_ms=int((perf_counter() - started) * 1000),
        )
