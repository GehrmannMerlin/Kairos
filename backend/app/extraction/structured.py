"""Structured deterministic extractors (D-010 第一级)：JSON-LD / Meta / Table。

All three return the shared ExtractionResult. Field mapping is a deterministic
canonical-hint table, never an LLM interpretation. Free-text meta description is
only used as a business field when the field explicitly maps to "description";
it is never auto-fantasized into multiple business fields.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from time import perf_counter
from typing import Any

from parsel import Selector

from app.domain.spec import FieldSpec
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

# 常见中文字段名 → canonical JSON-LD/Meta property
_FIELD_PROPERTY_HINTS: dict[str, str] = {
    "公司名": "name",
    "公司名称": "name",
    "企业名称": "name",
    "名称": "name",
    "品牌": "name",
    "官网": "url",
    "官网地址": "url",
    "网站": "url",
    "网址": "url",
    "官方网站": "url",
    "电话": "telephone",
    "电话号码": "telephone",
    "联系电话": "telephone",
    "手机": "telephone",
    "邮箱": "email",
    "电子邮件": "email",
    "联系邮箱": "email",
    "地址": "address",
    "公司地址": "address",
    "注册地址": "address",
    "城市": "city",
    "省份": "region",
    "国家": "country",
    "主营产品": "description",
    "业务": "description",
    "简介": "description",
    "主营业务": "description",
}

_JSONLD_ALIASES: dict[str, set[str]] = {
    "name": {"name", "title", "legalname", "company", "organization", "brand"},
    "url": {"url", "website", "web", "homepage", "officialsiteurl"},
    "telephone": {"telephone", "phone", "phonenumber", "tel"},
    "email": {"email", "emailaddress", "contactemail"},
    "address": {"address", "streetaddress", "addresslocality", "addressregion", "postalcode"},
    "city": {"city", "addresslocality"},
    "region": {"region", "addressregion", "province"},
    "country": {"country", "countryname"},
    "description": {"description", "slogan", "business", "product"},
}

_META_SELECTORS: dict[str, str] = {
    "name": (
        'meta[property="og:site_name"]::attr(content), '
        'meta[name="twitter:site"]::attr(content), '
        'meta[name="author"]::attr(content)'
    ),
    "url": (
        'meta[property="og:url"]::attr(content), '
        'meta[name="twitter:url"]::attr(content), '
        'link[rel="canonical"]::attr(href)'
    ),
    "telephone": 'meta[name="telephone"]::attr(content), meta[name="tel"]::attr(content)',
    "email": 'meta[name="email"]::attr(content), meta[property="og:email"]::attr(content)',
    "description": (
        'meta[name="description"]::attr(content), meta[property="og:description"]::attr(content)'
    ),
    "address": 'meta[name="address"]::attr(content), meta[property="og:locality"]::attr(content)',
}


def _property_for_field(field: FieldSpec) -> str | None:
    name_norm = (field.name or "").strip().lower()
    if name_norm in _FIELD_PROPERTY_HINTS:
        return _FIELD_PROPERTY_HINTS[name_norm]
    for prop, aliases in _JSONLD_ALIASES.items():
        if any(a in name_norm for a in aliases):
            return prop
    if field.description:
        desc = field.description.strip().lower()
        for prop, aliases in _JSONLD_ALIASES.items():
            if any(a in desc for a in aliases):
                return prop
    return None


def _coerce_scalar(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("@value", "name", "telephone", "url", "email"):
            if key in value and value[key] not in (None, ""):
                return str(value[key]).strip()
        if isinstance(value.get("address"), dict):
            parts = [
                str(v).strip()
                for v in value["address"].values()
                if isinstance(v, (str, int, float)) and str(v).strip()
            ]
            return ", ".join(parts) or None
    return None


def _jsonld_value(data: Any, prop: str, depth: int = 3) -> str | None:
    """Deterministic search of a schema.org tree for a canonical property value."""
    if depth < 0:
        return None
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.lower() == prop:
                found = _coerce_scalar(value)
                if found:
                    return found
        for value in data.values():
            found = _jsonld_value(value, prop, depth - 1)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _jsonld_value(item, prop, depth - 1)
            if found:
                return found
    return None


def _make_candidate(
    field: FieldSpec,
    raw_value: str,
    method: ExtractorMethod,
    locator: str | None,
    settings: ExtractionSettings,
) -> ExtractionCandidate:
    normalized = normalize_value(raw_value, field.type)
    return ExtractionCandidate(
        field_name=field.name,
        raw_value=raw_value,
        normalized_value=normalized,
        value_type=field.type.value,
        method=method,
        confidence=final_confidence(method, schema_valid=normalized is not None),
        extractor_version=settings.extractor_version,
        source_locator=locator,
        raw_snippet=raw_value[: settings.max_snippet_chars],
    )


class JsonLdExtractor:
    name = "json_ld"
    version = "1.0.0"

    async def extract(self, ctx: ExtractionContext, *, unresolved: list[str]) -> ExtractionResult:
        started = perf_counter()
        candidates: list[ExtractionCandidate] = []
        issues: list[ExtractionIssue] = []
        remaining = list(unresolved)
        documents: list[Any] = []
        try:
            scripts = (
                Selector(text=ctx.html).css('script[type="application/ld+json"]::text').getall()
            )
        except Exception:
            scripts = []
        for script in scripts:
            try:
                parsed = json.loads(script)
            except json.JSONDecodeError:
                issues.append(
                    ExtractionIssue(code="JSONLD_PARSE_FAILED", detail="JSON-LD script 解析失败")
                )
                continue
            if isinstance(parsed, dict):
                if isinstance(parsed.get("@graph"), list):
                    documents.extend(parsed["@graph"])
                else:
                    documents.append(parsed)
            elif isinstance(parsed, list):
                documents.extend(parsed)
        for field in ctx.fields:
            if field.name not in remaining:
                continue
            prop = _property_for_field(field)
            if prop is None:
                continue
            value: str | None = None
            locator: str | None = None
            for idx, doc in enumerate(documents):
                found = _jsonld_value(doc, prop)
                if found:
                    value = found
                    locator = f"jsonld[{idx}]/{prop}"
                    break
            if value is None:
                continue
            candidates.append(
                _make_candidate(field, value, ExtractorMethod.JSON_LD, locator, ctx.settings)
            )
            remaining.remove(field.name)
        return ExtractionResult(
            snapshot_id=ctx.snapshot_ref.snapshot_id,
            schema_version=ctx.settings.schema_version,
            extractor_type=self.name,
            extractor_version=self.version,
            candidates=candidates,
            unresolved_fields=remaining,
            issues=issues,
            duration_ms=int((perf_counter() - started) * 1000),
        )


class MetaExtractor:
    name = "meta"
    version = "1.0.0"

    async def extract(self, ctx: ExtractionContext, *, unresolved: list[str]) -> ExtractionResult:
        started = perf_counter()
        candidates: list[ExtractionCandidate] = []
        remaining = list(unresolved)
        sel = Selector(text=ctx.html)
        for field in ctx.fields:
            if field.name not in remaining:
                continue
            prop = _property_for_field(field)
            if prop is None or prop not in _META_SELECTORS:
                continue
            value = None
            for expr in _META_SELECTORS[prop].split(","):
                found = sel.css(expr.strip()).get()
                if found:
                    value = found.strip()
                    break
            if not value:
                continue
            candidates.append(
                _make_candidate(field, value, ExtractorMethod.META, f"meta/{prop}", ctx.settings)
            )
            remaining.remove(field.name)
        return ExtractionResult(
            snapshot_id=ctx.snapshot_ref.snapshot_id,
            schema_version=ctx.settings.schema_version,
            extractor_type=self.name,
            extractor_version=self.version,
            candidates=candidates,
            unresolved_fields=remaining,
            issues=[],
            duration_ms=int((perf_counter() - started) * 1000),
        )


class TableExtractor:
    name = "table"
    version = "1.0.0"

    async def extract(self, ctx: ExtractionContext, *, unresolved: list[str]) -> ExtractionResult:
        started = perf_counter()
        candidates: list[ExtractionCandidate] = []
        remaining = list(unresolved)
        sel = Selector(text=ctx.html)
        all_rows = sel.xpath("//table//tr")
        # header-row tables: a row whose cells are mostly known field labels
        header_cells: list[str] = []
        data_row: list[str] | None = None
        for r_idx, row in enumerate(all_rows):
            cells = [
                normalize_text(c)
                for c in row.xpath(".//th//text() | .//td//text()").getall()
                if normalize_text(c)
            ]
            known = [c for c in cells if self._label_matches_field(c, ctx.fields)]
            if len(known) >= 2 and len(cells) >= 2:
                header_cells = cells
                if r_idx + 1 < len(all_rows):
                    data_row = [
                        normalize_text(c)
                        for c in all_rows[r_idx + 1].xpath(".//th//text() | .//td//text()").getall()
                        if normalize_text(c)
                    ]
                break
        if header_cells and data_row:
            for field in ctx.fields:
                if field.name not in remaining:
                    continue
                for col_idx, header in enumerate(header_cells):
                    if self._label_matches_field(header, [field]) and col_idx < len(data_row):
                        value = data_row[col_idx]
                        if value:
                            candidates.append(
                                _make_candidate(
                                    field,
                                    value,
                                    ExtractorMethod.TABLE,
                                    f"table[0]/{header}",
                                    ctx.settings,
                                )
                            )
                            remaining.remove(field.name)
                        break
        # key-value rows fallback: first cell = label, following cells = value
        key_value_rows: list[tuple[str, str, int]] = []
        for r_idx, row in enumerate(all_rows):
            cells = [
                normalize_text(c)
                for c in row.xpath(".//th//text() | .//td//text()").getall()
                if normalize_text(c)
            ]
            if len(cells) >= 2:
                key_value_rows.append((cells[0], " ".join(cells[1:]), r_idx))
        for field in ctx.fields:
            if field.name not in remaining:
                continue
            for label, value, r_idx in key_value_rows:
                if self._label_matches_field(label, [field]):
                    candidates.append(
                        _make_candidate(
                            field,
                            value,
                            ExtractorMethod.TABLE,
                            f"table[0]/row{r_idx}",
                            ctx.settings,
                        )
                    )
                    remaining.remove(field.name)
                    break
        return ExtractionResult(
            snapshot_id=ctx.snapshot_ref.snapshot_id,
            schema_version=ctx.settings.schema_version,
            extractor_type=self.name,
            extractor_version=self.version,
            candidates=candidates,
            unresolved_fields=remaining,
            issues=[],
            duration_ms=int((perf_counter() - started) * 1000),
        )

    @staticmethod
    def _label_matches_field(label: str, fields: Iterable[FieldSpec]) -> bool:
        lbl = label.strip().lower()
        for f in fields:
            name = f.name.strip().lower()
            if name == lbl or name in lbl or lbl in name:
                return True
        return False
