/** M-15 Artifact / Export / Completion 前端类型（对应 backend app/artifacts/contracts.py）。 */

export type ExportType = 'formal' | 'review' | 'audit'
export type ExportScope = 'current' | 'all'

export interface ExportFilter {
  q?: string | null
  field?: string | null
  value?: string | null
  source_type?: string | null
  extract_method?: string | null
  min_confidence?: number | null
  review_type?: string | null
}

export interface ExportRequest {
  export_type: ExportType
  scope: ExportScope
  filter: ExportFilter
}

export interface ArtifactRef {
  artifact_id: number
  content_hash: string
  download_url: string
  row_count: number
}

export interface ArtifactView {
  artifact_id: number
  export_type: string
  dataset_version: string
  filter_snapshot: Record<string, unknown>
  schema_version: string | null
  row_count: number
  size_bytes: number | null
  content_hash: string
  filename: string
  status: string
  created_at: string
  download_url: string
}

export interface CompletionCardView {
  task_id: number
  completion_id: number | null
  status: string
  reason: string | null
  completion_type: string | null
  is_partial: boolean
  qualified_record_count: number
  partition_counts: Record<string, number>
  url_processed: number
  runtime_limit_reason: string | null
  scope_completion_metadata: Record<string, unknown>
  can_view_data: boolean
  can_view_quality: boolean
  can_export_formal: boolean
  can_export_review: boolean
}
