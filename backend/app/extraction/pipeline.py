"""ExtractionPipeline — 提取阶梯编排（D-010），字段级 fallback。

Structured → Verified Site Rules → LLM fallback；只有 unresolved 字段继续下发。
确定性已验证值不被低优先级 extractor 静默覆盖（十一）。LLM 输出经过 grounding +
schema validation，绝不直接写有效候选（三十二）。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from app.domain.models import PageSnapshot
from app.domain.spec import FieldType
from app.extraction.confidence import final_confidence
from app.extraction.context import ExtractionContextBuilder
from app.extraction.contracts import (
    ExtractionCandidate,
    ExtractionIssue,
    ExtractionResult,
    ExtractionSettings,
    ExtractorMethod,
)
from app.extraction.grounding import evidence_is_grounded
from app.extraction.llm import SemanticExtractionAgent, SemanticExtractionInput
from app.extraction.normalize import normalize_value
from app.extraction.schema_validator import ExtractionSchemaValidator
from app.extraction.site_rules import SiteRuleExtractor
from app.extraction.structured import JsonLdExtractor, MetaExtractor, TableExtractor
from app.infra.object_storage import ObjectStorage


class ExtractionPipeline:
    def __init__(
        self,
        db: Any,
        storage: ObjectStorage,
        *,
        context_builder: ExtractionContextBuilder | None = None,
        structured: tuple[Any, ...] | None = None,
        site_rules: Any | None = None,
        llm_agent: Any | None = None,
        validator: ExtractionSchemaValidator | None = None,
        settings: ExtractionSettings | None = None,
        model_audit: dict | None = None,
    ) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings or ExtractionSettings()
        self._model_audit = model_audit or {}
        self._context_builder = context_builder or ExtractionContextBuilder(
            db, storage, self._settings
        )
        self._structured = structured or (
            JsonLdExtractor(),
            MetaExtractor(),
            TableExtractor(),
        )
        self._site_rules = site_rules or SiteRuleExtractor(db, self._settings)
        self._llm_agent = llm_agent or SemanticExtractionAgent(settings=self._settings)
        self._validator = validator or ExtractionSchemaValidator()

    async def run(
        self, snapshot: PageSnapshot, spec_payload: dict, *, user_id: int
    ) -> ExtractionResult:
        started = perf_counter()
        ctx = await self._context_builder.build(snapshot, spec_payload)
        fields_by_name = {f.name: f for f in ctx.fields}
        unresolved = [f.name for f in ctx.fields]
        all_candidates: list[ExtractionCandidate] = []
        all_issues: list[ExtractionIssue] = []
        llm_invocations = 0

        # 1) structured（JSON-LD / Meta / Table）
        for extractor in self._structured:
            if not unresolved:
                break
            result = await extractor.extract(ctx, unresolved=unresolved)
            self._merge(result, all_candidates, all_issues, unresolved, fields_by_name)

        # 2) verified site rules（只处理 unresolved）
        if unresolved:
            result = await self._site_rules.extract(ctx, unresolved=unresolved)
            self._merge(result, all_candidates, all_issues, unresolved, fields_by_name)

        # 3) LLM fallback（unresolved fields only，字段级，绝不页面级重发）
        if unresolved and self._settings.allow_llm_fallback:
            llm_invocations += 1
            inp = SemanticExtractionInput(
                schema_version=self._settings.schema_version,
                fields=[f.model_dump(mode="json") for f in ctx.fields if f.name in unresolved],
                unresolved_fields=unresolved,
                known_candidates=[
                    {
                        "field": c.field_name,
                        "value": c.normalized_value or c.raw_value,
                        "method": c.method.value,
                    }
                    for c in all_candidates
                ],
                readable_text=ctx.readable_text,
                source_url=ctx.snapshot_ref.final_url or ctx.snapshot_ref.url,
                snapshot_id=ctx.snapshot_ref.snapshot_id,
                run_id=ctx.snapshot_ref.run_id,
            )
            llm_result = await self._llm_agent.extract(inp)
            for cand in llm_result.fields:
                field = fields_by_name.get(cand.field_name)
                if field is None:
                    all_issues.append(
                        ExtractionIssue(
                            code="LLM_UNKNOWN_FIELD",
                            field_name=cand.field_name,
                            method=ExtractorMethod.LLM,
                        )
                    )
                    continue
                if not cand.value:
                    all_issues.append(
                        ExtractionIssue(
                            code="LLM_MISSING_VALUE",
                            field_name=field.name,
                            method=ExtractorMethod.LLM,
                        )
                    )
                    continue
                # URL 字段（如 原文URL）的值是页面自身 URL，不在正文中，文本 grounding
                # 不适用；URL 格式校验（normalize_url 返回 None 即 schema 拒绝）已防幻觉
                # （DEPLOY-GATE-3 上海政府：所有含 原文URL 的 record 均被 grounding 拦截）。
                if field.type != FieldType.URL and not evidence_is_grounded(
                    cand.evidence_quote, ctx.readable_text
                ):
                    all_issues.append(
                        ExtractionIssue(
                            code="EVIDENCE_NOT_GROUNDED",
                            field_name=field.name,
                            detail="LLM evidence quote 不在页面上下文中",
                            method=ExtractorMethod.LLM,
                        )
                    )
                    continue
                normalized = normalize_value(cand.value, field.type)
                candidate = ExtractionCandidate(
                    field_name=field.name,
                    raw_value=cand.value,
                    normalized_value=normalized,
                    value_type=field.type.value,
                    method=ExtractorMethod.LLM,
                    confidence=final_confidence(
                        ExtractorMethod.LLM,
                        schema_valid=normalized is not None,
                        grounded=True,
                        llm_confidence=cand.confidence,
                    ),
                    extractor_version=self._settings.extractor_version,
                    model_config_id=self._model_audit.get("model_config_id"),
                    source_locator=cand.source_locator,
                    raw_snippet=(cand.evidence_quote or cand.value)[
                        : self._settings.max_snippet_chars
                    ],
                )
                schema_issue = self._validator.validate(candidate, field)
                if schema_issue is not None:
                    all_issues.append(schema_issue)
                    continue
                all_candidates.append(candidate)
                unresolved.remove(field.name)

        all_candidates = self._dedupe_per_field_method(all_candidates)
        return ExtractionResult(
            snapshot_id=ctx.snapshot_ref.snapshot_id,
            schema_version=self._settings.schema_version,
            extractor_type="ladder",
            extractor_version=self._settings.extractor_version,
            candidates=all_candidates,
            unresolved_fields=unresolved,
            issues=all_issues,
            duration_ms=int((perf_counter() - started) * 1000),
            technical_metadata={"llm_invocations": llm_invocations, "user_id": user_id},
        )

    def _merge(
        self,
        result: ExtractionResult,
        all_candidates: list[ExtractionCandidate],
        all_issues: list[ExtractionIssue],
        unresolved: list[str],
        fields_by_name: dict[str, Any],
    ) -> None:
        for cand in result.candidates:
            field = fields_by_name.get(cand.field_name)
            if field is None:
                continue
            schema_issue = self._validator.validate(cand, field)
            if schema_issue is not None:
                all_issues.append(schema_issue)
                continue
            all_candidates.append(cand)
            if cand.field_name in unresolved:
                unresolved.remove(cand.field_name)
        all_issues.extend(result.issues)

    @staticmethod
    def _dedupe_per_field_method(
        candidates: list[ExtractionCandidate],
    ) -> list[ExtractionCandidate]:
        seen: dict[tuple[str, str], ExtractionCandidate] = {}
        for c in candidates:
            key = (c.field_name, c.method.value)
            if key not in seen or c.confidence > seen[key].confidence:
                seen[key] = c
        return list(seen.values())
