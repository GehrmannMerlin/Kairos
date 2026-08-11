"""规则学习（二十一~二十四）：LLM 只提出候选；程序验证后才 Promote。

Rule Candidate → 代表性 PageSnapshot 验证 → schema/evidence/质量阈值 → ACTIVE。
未验证 Rule 不能进入批量 production extraction；失败不永久删除，可回退（二十五）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from parsel import Selector
from pydantic import BaseModel, ConfigDict, Field

from app.extraction.contracts import ExtractionSettings
from app.extraction.repository import ExtractorRuleRepository
from app.extraction.site_rules import apply_value_transform, text_value
from app.infra.object_storage import ObjectStorage

_STRICT = ConfigDict(extra="forbid")


class RuleCandidate(BaseModel):
    model_config = _STRICT

    site_host: str
    field_name: str
    rule_type: str  # css | xpath
    selector: str
    value_transform: str = "identity"
    samples: list[dict] = Field(default_factory=list)  # [{snapshot_id, value, quote}]


class RuleValidationResult(BaseModel):
    model_config = _STRICT

    candidate: RuleCandidate
    samples_checked: int
    matches: int
    coverage: float  # fraction of samples where the selector produced any value
    precision: float  # fraction of samples where the produced value matched expected
    pass_threshold: bool
    detail: str = ""


@dataclass
class RuleLearningService:
    db: Any
    storage: ObjectStorage
    user_id: int = 0
    settings: ExtractionSettings = field(default_factory=ExtractionSettings)

    def __post_init__(self) -> None:
        self._repo = ExtractorRuleRepository(self.db)

    async def validate_representative(self, candidate: RuleCandidate) -> RuleValidationResult:
        """Apply the rule to each sample snapshot and compare to the expected value."""
        matches = 0
        produced = 0
        for sample in candidate.samples:
            snapshot_id = sample["snapshot_id"]
            expected = (sample.get("value") or "").strip()
            snapshot = self._snapshot(snapshot_id)
            if snapshot is None or not snapshot.storage_ref:
                continue
            raw = await self.storage.get(snapshot.storage_ref)
            html = raw.decode("utf-8", errors="ignore")
            sel = Selector(text=html)
            try:
                parts = (
                    sel.css(candidate.selector).getall()
                    if candidate.rule_type == "css"
                    else sel.xpath(candidate.selector).getall()
                )
            except Exception:
                parts = []
            if parts:
                produced += 1
                actual = apply_value_transform(
                    text_value(parts[0]), candidate.value_transform
                ).strip()
                if expected and actual == expected:
                    matches += 1
        checked = max(len(candidate.samples), 1)
        coverage = produced / checked
        precision = matches / checked
        pass_threshold = (
            checked >= self.settings.min_rule_validation_samples
            and precision >= self.settings.min_rule_precision
            and coverage >= self.settings.min_rule_coverage
        )
        return RuleValidationResult(
            candidate=candidate,
            samples_checked=len(candidate.samples),
            matches=matches,
            coverage=coverage,
            precision=precision,
            pass_threshold=pass_threshold,
            detail=(
                f"precision={precision:.2f} coverage={coverage:.2f} "
                f"samples={len(candidate.samples)} "
                f"threshold={self.settings.min_rule_validation_samples}"
            ),
        )

    def promote(self, result: RuleValidationResult, *, schema_valid: bool) -> Any | None:
        """Only schema-valid + threshold-passed rules become ACTIVE (二十三)."""
        if not schema_valid or not result.pass_threshold:
            return None
        candidate = result.candidate
        previous = self._repo.latest_for_field(
            user_id=self.user_id, site_host=candidate.site_host, field_name=candidate.field_name
        )
        version = self._repo.next_version(
            user_id=self.user_id, site_host=candidate.site_host, field_name=candidate.field_name
        )
        rule = self._repo.create(
            user_id=self.user_id,
            site_host=candidate.site_host,
            field_name=candidate.field_name,
            schema_identity=None,
            rule_type=candidate.rule_type,
            selector=candidate.selector,
            value_transform=candidate.value_transform,
            version=version,
            status="ACTIVE",
            quality={
                "precision": result.precision,
                "coverage": result.coverage,
                "samples": result.samples_checked,
                "validated_snapshot_ids": [s["snapshot_id"] for s in candidate.samples],
            },
            supersedes_version_id=previous.id if previous is not None else None,
        )
        if previous is not None and previous.id != rule.id:
            self._repo.set_status(previous, "STALE")
        return rule

    def rollback(self, *, site_host: str, field_name: str, to_version: int) -> None:
        """Set the target version ACTIVE and demote any newer ACTIVE rule to STALE."""
        from sqlalchemy import select

        from app.domain.models import ExtractorRuleVersion

        row = self.db.scalar(
            select(ExtractorRuleVersion).where(
                ExtractorRuleVersion.user_id == self.user_id,
                ExtractorRuleVersion.site_host == site_host,
                ExtractorRuleVersion.field_name == field_name,
                ExtractorRuleVersion.version == to_version,
            )
        )
        if row is None:
            return
        for r in self._repo.active_for_fields(
            user_id=self.user_id, site_host=site_host, field_names=[field_name]
        ):
            if r.version != to_version:
                self._repo.set_status(r, "STALE")
        self._repo.set_status(row, "ACTIVE")

    def _snapshot(self, snapshot_id: int) -> Any | None:
        from app.domain.models import PageSnapshot

        snapshot = self.db.get(PageSnapshot, snapshot_id)
        if snapshot is None or snapshot.user_id != self.user_id:
            return None
        return snapshot
