/** M-14 Quality 工作区类型（对应后端 app/quality/contracts.py）。 */

export interface QualityDrilldown {
  status?: 'passed' | 'review' | 'rejected' | null
  review_type?: string | null
  source_type?: string | null
  extract_method?: string | null
  min_confidence?: number | null
}

export interface QualityMetricItem {
  key: string
  label: string
  value: number
  kind: 'count' | 'rate'
  drilldown: QualityDrilldown
}

export interface QualitySummary {
  total_records: number
  passed: number
  needs_review: number
  rejected: number
}

export interface QualityMetricsDto {
  pass_rate: number
  missing_rate: number
  duplicate_rate: number
  conflict_count: number
  source_coverage: number
  sampling_accuracy: number | null
}

export interface QualityDiagnostics {
  missing_required: number
  unresolved_conflict: number
  possible_duplicate: number
  low_confidence: number
  rejected: number
}

export interface FieldCompletenessRow {
  field_name: string
  total: number
  non_null: number
  missing: number
  completion_rate: number
}

export interface SourceCoverageRow {
  source_type: string
  eligible: boolean
  covered: boolean
  record_count: number
}

export interface SamplingSummary {
  sample_count: number
  accuracy: number | null
  sample_refs: Record<string, unknown>[]
}

export interface QualityView {
  task_id: number
  dataset_version: string | null
  validation_version: string | null
  sampling_policy_version: string | null
  spec_version: number | null
  run_id: number | null
  snapshot_id: number | null
  snapshot_created_at: string | null
  summary: QualitySummary
  metrics: QualityMetricsDto
  field_completeness: FieldCompletenessRow[]
  source_coverage: SourceCoverageRow[]
  diagnostics: QualityDiagnostics
  sampling: SamplingSummary
  items: QualityMetricItem[]
}
