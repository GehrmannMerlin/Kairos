"""M-11 persistence: Record candidate + FieldEvidence + ExtractorRuleVersion.

All create() methods flush (no commit) so the executor can commit Record +
candidates + evidence + DomainEvents in one transaction (D-015 / 四十七).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.domain.models import (
    ExtractorRuleVersion,
    FieldEvidence,
    PageSnapshot,
    Record,
)
from app.extraction.contracts import RecordPartition


class FieldEvidenceRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        record_id: int,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
        snapshot_id: int,
        url_resource_id: int | None,
        field_name: str,
        value: str,
        normalized_value: str,
        value_type: str,
        source_url: str,
        source_locator: str | None,
        raw_snippet: str,
        extract_method: str,
        extractor_version: str,
        rule_version_id: int | None,
        model_config_id: str | None,
        confidence: float,
        evidence_hash: str,
        validation_status: str,
        issue_code: str | None,
    ) -> FieldEvidence:
        row = FieldEvidence(
            record_id=record_id,
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            spec_version=spec_version,
            snapshot_id=snapshot_id,
            url_resource_id=url_resource_id,
            field_name=field_name,
            value=value,
            normalized_value=normalized_value,
            value_type=value_type,
            source_url=source_url,
            source_locator=source_locator,
            raw_snippet=raw_snippet,
            extract_method=extract_method,
            extractor_version=extractor_version,
            rule_version_id=rule_version_id,
            model_config_id=model_config_id,
            confidence=confidence,
            evidence_hash=evidence_hash,
            validation_status=validation_status,
            issue_code=issue_code,
        )
        self._db.add(row)
        return row

    def list_for_record(self, user_id: int, record_id: int) -> list[FieldEvidence]:
        return list(
            self._db.scalars(
                select(FieldEvidence).where(
                    FieldEvidence.user_id == user_id, FieldEvidence.record_id == record_id
                )
            )
        )

    def list_for_snapshot(self, user_id: int, snapshot_id: int) -> list[FieldEvidence]:
        return list(
            self._db.scalars(
                select(FieldEvidence).where(
                    FieldEvidence.user_id == user_id, FieldEvidence.snapshot_id == snapshot_id
                )
            )
        )


class ExtractorRuleRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: int,
        site_host: str,
        field_name: str,
        schema_identity: str | None,
        rule_type: str,
        selector: str,
        value_transform: str = "identity",
        version: int,
        status: str = "draft",
        quality: dict | None = None,
        supersedes_version_id: int | None = None,
    ) -> ExtractorRuleVersion:
        row = ExtractorRuleVersion(
            user_id=user_id,
            site_host=site_host,
            field_name=field_name,
            schema_identity=schema_identity,
            rule_type=rule_type,
            selector=selector,
            value_transform=value_transform,
            version=version,
            status=status,
            quality=quality,
            supersedes_version_id=supersedes_version_id,
        )
        self._db.add(row)
        return row

    def next_version(self, *, user_id: int, site_host: str, field_name: str) -> int:
        rows = self._db.scalars(
            select(ExtractorRuleVersion).where(
                ExtractorRuleVersion.user_id == user_id,
                ExtractorRuleVersion.site_host == site_host,
                ExtractorRuleVersion.field_name == field_name,
            )
        )
        return max((r.version for r in rows), default=0) + 1

    def active_for_fields(
        self, *, user_id: int, site_host: str, field_names: list[str]
    ) -> list[ExtractorRuleVersion]:
        if not field_names:
            return []
        return list(
            self._db.scalars(
                select(ExtractorRuleVersion).where(
                    ExtractorRuleVersion.user_id == user_id,
                    ExtractorRuleVersion.site_host == site_host,
                    ExtractorRuleVersion.field_name.in_(field_names),
                    ExtractorRuleVersion.status == "ACTIVE",
                )
            )
        )

    def latest_for_field(
        self, *, user_id: int, site_host: str, field_name: str
    ) -> ExtractorRuleVersion | None:
        return self._db.scalar(
            select(ExtractorRuleVersion)
            .where(
                ExtractorRuleVersion.user_id == user_id,
                ExtractorRuleVersion.site_host == site_host,
                ExtractorRuleVersion.field_name == field_name,
            )
            .order_by(ExtractorRuleVersion.version.desc())
            .limit(1)
        )

    def set_status(self, rule: ExtractorRuleVersion, status: str) -> ExtractorRuleVersion:
        rule.status = status
        self._db.add(rule)
        return rule

    def increment_failure(self, rule: ExtractorRuleVersion) -> ExtractorRuleVersion:
        rule.failure_count += 1
        self._db.add(rule)
        return rule


class ExtractionRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create_record(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
        url_resource_id: int | None,
        payload: dict,
    ) -> Record:
        row = Record(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            spec_version=spec_version,
            url_resource_id=url_resource_id,
            payload=payload,
            partition=RecordPartition.EXTRACTED.value,
            business_key=None,
        )
        self._db.add(row)
        self._db.flush()  # 取 PK，供同一事务内写入 FieldEvidence（单事务提交）
        return row

    def records_for_task(self, user_id: int, task_id: int) -> list[Record]:
        return list(
            self._db.scalars(
                select(Record).where(
                    Record.user_id == user_id,
                    Record.task_id == task_id,
                    Record.partition == RecordPartition.EXTRACTED.value,
                )
            )
        )

    def snapshot_already_extracted(self, user_id: int, task_id: int, snapshot_id: int) -> bool:
        records = self.records_for_task(user_id, task_id)
        return any((r.payload or {}).get("snapshot_id") == snapshot_id for r in records)

    def mark_records_eligible_for_recompute(
        self, user_id: int, task_id: int, field_name: str, rule_version: int
    ) -> int:
        """Mark records whose evidence references a rolled-back rule version (M-12 recompute)."""
        records = self.records_for_task(user_id, task_id)
        count = 0
        for record in records:
            payload = dict(record.payload or {})  # 新 dict，确保 ORM 检测到 JSON 变更
            rules = payload.get("rule_versions") or {}
            if rules.get(field_name) == rule_version:
                payload["recompute_eligible"] = True
                record.payload = payload
                self._db.add(record)
                count += 1
        return count

    def pending_snapshots(
        self, *, user_id: int, task_id: int, limit: int = 50
    ) -> list[PageSnapshot]:
        from app.domain.models import PageSnapshot as PS

        extracted = {r.payload.get("snapshot_id") for r in self.records_for_task(user_id, task_id)}
        rows = list(
            self._db.scalars(
                select(PS)
                .where(PS.user_id == user_id, PS.task_id == task_id)
                .where(PS.extraction_status.is_(None))
                .order_by(PS.id)
            )
        )
        return [r for r in rows if r.id not in extracted][:limit]

    def mark_snapshot_extraction_failed(self, snapshot_id: int) -> None:
        """记录快照的合法提取失败（M-11 失败账本），同一事务提交由调用方负责。

        让后续小批次不再重复处理同一失败快照（避免 MORE_PENDING 重跑无限循环）。
        """
        from app.domain.models import PageSnapshot as PS

        row = self._db.get(PS, snapshot_id)
        if row is not None:
            row.extraction_status = "failed"
            self._db.add(row)
