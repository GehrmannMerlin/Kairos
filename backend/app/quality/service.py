"""M-14 Quality Query service：装配只读 QualityView + typed drilldown（D-062）。

- summary/diagnostics 来自当前 DB facts（Record 列）。
- metrics 优先来自最新 QualitySnapshot（冻结报告，绑定 dataset/validation/sampling 版本）；
  无 snapshot 时按当前 facts 实时计算（同样全部来自 DB）。
- field_completeness / source_coverage / sampling 均来自数据库事实；页面打开不重新抽样。
- 前端只渲染，不计算业务事实。
"""

from __future__ import annotations

from typing import Any

from app.quality.contracts import (
    FieldCompletenessRow,
    QualityDiagnostics,
    QualityDrilldown,
    QualityMetricItem,
    QualityMetricsDto,
    QualitySummary,
    QualityView,
    SamplingSummary,
    SourceCoverageRow,
)
from app.quality.repository import QualityRepository


class QualityService:
    def __init__(self, db: Any) -> None:
        self._repo = QualityRepository(db)

    def assemble(self, *, user_id: int, task_id: int) -> QualityView:
        snapshot = self._repo.latest_snapshot(user_id=user_id, task_id=task_id)
        counts = self._repo.count_by_partition(user_id=user_id, task_id=task_id)
        passed = counts.get("passed", 0)
        review = counts.get("needs_review", 0)
        rejected = counts.get("rejected", 0)
        total = passed + review + rejected

        diagnostics = QualityDiagnostics(
            missing_required=self._repo.count_review_type(
                user_id=user_id, task_id=task_id, review_type="missing_required"
            ),
            unresolved_conflict=self._repo.count_review_type(
                user_id=user_id, task_id=task_id, review_type="unresolved_conflict"
            ),
            possible_duplicate=self._repo.count_review_type(
                user_id=user_id, task_id=task_id, review_type="possible_duplicate"
            ),
            low_confidence=self._repo.count_review_type(
                user_id=user_id, task_id=task_id, review_type="low_confidence"
            ),
            rejected=rejected,
        )

        summary = QualitySummary(
            total_records=total, passed=passed, needs_review=review, rejected=rejected
        )
        metrics, sampling = self._metrics_and_sampling(
            user_id=user_id,
            task_id=task_id,
            snapshot=snapshot,
            summary=summary,
            diagnostics=diagnostics,
        )
        field_completeness = self._field_completeness(user_id=user_id, task_id=task_id)
        source_rows = self._source_coverage(user_id=user_id, task_id=task_id)
        items = self._items(summary=summary, diagnostics=diagnostics, sources=source_rows)

        return QualityView(
            task_id=task_id,
            dataset_version=snapshot.dataset_version if snapshot else None,
            validation_version=snapshot.validation_version if snapshot else None,
            sampling_policy_version=snapshot.sampling_policy_version if snapshot else None,
            spec_version=snapshot.spec_version if snapshot else None,
            run_id=snapshot.run_id if snapshot else None,
            snapshot_id=snapshot.id if snapshot else None,
            snapshot_created_at=snapshot.created_at if snapshot else None,
            summary=summary,
            metrics=metrics,
            field_completeness=field_completeness,
            source_coverage=source_rows,
            diagnostics=diagnostics,
            sampling=sampling,
            items=items,
        )

    def _metrics_and_sampling(self, *, user_id, task_id, snapshot, summary, diagnostics):
        if snapshot is not None:
            m = snapshot.metrics or {}
            metrics = QualityMetricsDto(
                pass_rate=float(m.get("pass_rate") or 0.0),
                missing_rate=float(m.get("missing_rate") or 0.0),
                duplicate_rate=float(m.get("duplicate_rate") or 0.0),
                conflict_count=int(m.get("conflict_count") or 0),
                source_coverage=float(m.get("source_coverage") or 0.0),
                sampling_accuracy=(
                    float(m["sampling_accuracy"])
                    if m.get("sampling_accuracy") is not None
                    else None
                ),
            )
            refs = list(snapshot.sample_refs or [])
            sampling = SamplingSummary(
                sample_count=len(refs), accuracy=metrics.sampling_accuracy, sample_refs=refs
            )
            return metrics, sampling

        total = summary.total_records
        conflict = self._repo.unresolved_conflict_count(user_id=user_id, task_id=task_id)
        eligible, covered = self._source_facts(user_id=user_id, task_id=task_id)
        metrics = QualityMetricsDto(
            pass_rate=round(summary.passed / total, 4) if total else 0.0,
            missing_rate=round(diagnostics.missing_required / total, 4) if total else 0.0,
            duplicate_rate=round(diagnostics.possible_duplicate / total, 4) if total else 0.0,
            conflict_count=conflict,
            source_coverage=round(covered / eligible, 4) if eligible else 0.0,
            sampling_accuracy=None,
        )
        return metrics, SamplingSummary(sample_count=0)

    def _source_facts(self, *, user_id: int, task_id: int) -> tuple[int, int]:
        urls = self._repo.url_resources(user_id=user_id, task_id=task_id)
        eligible = {u.source_type for u in urls}
        covered = {u.source_type for u in urls if u.status in ("FETCHED", "HANDED_OFF")}
        return len(eligible), len(covered)

    def _field_completeness(self, *, user_id: int, task_id: int) -> list[FieldCompletenessRow]:
        spec = self._repo.spec_for_task(user_id=user_id, task_id=task_id)
        if spec is None:
            return []
        fields = (spec.payload or {}).get("fields") or []
        records = self._repo.records_for_task(user_id=user_id, task_id=task_id)
        total = len(records)
        rows: list[FieldCompletenessRow] = []
        for f in fields:
            name = f.get("name") if isinstance(f, dict) else None
            if not name:
                continue
            non_null = sum(
                1
                for r in records
                if self._value_present(r, name)
            )
            rows.append(
                FieldCompletenessRow(
                    field_name=name,
                    total=total,
                    non_null=non_null,
                    missing=total - non_null,
                    completion_rate=round(non_null / total, 4) if total else 0.0,
                )
            )
        return rows

    @staticmethod
    def _value_present(record, field_name: str) -> bool:
        value = QualityRepository.record_field_value(record, field_name)
        return value is not None and str(value).strip() != ""

    def _source_coverage(self, *, user_id: int, task_id: int) -> list[SourceCoverageRow]:
        urls = self._repo.url_resources(user_id=user_id, task_id=task_id)
        url_by_id = {u.id: u for u in urls}
        records = self._repo.records_for_task(user_id=user_id, task_id=task_id)
        covered_types = {u.source_type for u in urls if u.status in ("FETCHED", "HANDED_OFF")}
        rows: list[SourceCoverageRow] = []
        for st in sorted({u.source_type for u in urls}):
            count = sum(1 for r in records if QualityRepository.record_source(r, url_by_id) == st)
            rows.append(
                SourceCoverageRow(
                    source_type=st,
                    eligible=True,
                    covered=st in covered_types,
                    record_count=count,
                )
            )
        return rows

    @staticmethod
    def _items(*, summary, diagnostics, sources) -> list[QualityMetricItem]:
        return [
            QualityMetricItem(
                key="passed",
                label="已通过",
                value=summary.passed,
                kind="count",
                drilldown=QualityDrilldown(status="passed"),
            ),
            QualityMetricItem(
                key="needs_review",
                label="待复核",
                value=summary.needs_review,
                kind="count",
                drilldown=QualityDrilldown(status="review"),
            ),
            QualityMetricItem(
                key="rejected",
                label="已拒绝",
                value=summary.rejected,
                kind="count",
                drilldown=QualityDrilldown(status="rejected"),
            ),
            QualityMetricItem(
                key="missing_required",
                label="字段缺失",
                value=diagnostics.missing_required,
                kind="count",
                drilldown=QualityDrilldown(status="review", review_type="missing_required"),
            ),
            QualityMetricItem(
                key="unresolved_conflict",
                label="来源冲突",
                value=diagnostics.unresolved_conflict,
                kind="count",
                drilldown=QualityDrilldown(status="review", review_type="unresolved_conflict"),
            ),
            QualityMetricItem(
                key="possible_duplicate",
                label="重复记录",
                value=diagnostics.possible_duplicate,
                kind="count",
                drilldown=QualityDrilldown(status="review", review_type="possible_duplicate"),
            ),
            QualityMetricItem(
                key="low_confidence",
                label="低置信度",
                value=diagnostics.low_confidence,
                kind="count",
                drilldown=QualityDrilldown(status="review", review_type="low_confidence"),
            ),
            *[
                QualityMetricItem(
                    key=f"source:{s.source_type}",
                    label=f"来源 {s.source_type}",
                    value=s.record_count,
                    kind="count",
                    drilldown=QualityDrilldown(source_type=s.source_type),
                )
                for s in sources
            ],
        ]
