/** M-13 数据工作区类型（对应后端 app/review/contracts.py）。 */

export type RecordPartition = 'passed' | 'needs_review' | 'rejected'

export interface RecordView {
  record_id: number
  task_id: number
  partition: RecordPartition
  review_type: string | null
  review_reason: string | null
  data_version: number
  fields: Record<string, string | number | boolean | null>
  source_url: string | null
  created_at: string
  updated_at: string
  allowed_actions: string[]
}

export interface RecordFieldDetail {
  field_name: string
  value: string | null
  original_value: string | null
  value_source: string
  extract_method: string | null
  extractor_version: string | null
  confidence: number | null
  source_url: string | null
  snapshot_id: number | null
}

export interface RecordDetailView {
  record_id: number
  task_id: number
  partition: RecordPartition
  review_type: string | null
  review_reason: string | null
  data_version: number
  allowed_actions: string[]
  fields: RecordFieldDetail[]
  created_at: string
  updated_at: string
}

export interface RecordListResponse {
  task_id: number
  partition_counts: Record<string, number>
  items: RecordView[]
  total: number
  page: number
  page_size: number
  dataset_version: string | null
}

export type ReviewAction = 'approve' | 'reject' | 'edit' | 'agent_reevaluate'

export interface FieldEdit {
  field_name: string
  final_value: string | null
}

export interface RecordReviewCommand {
  action: ReviewAction
  reason?: string | null
  edits?: FieldEdit[]
  expected_data_version: number
}

export interface RecordReviewResponse {
  record: RecordView
}

export interface BatchReviewCommand {
  action: 'approve' | 'reject' | 'agent_reevaluate'
  record_ids: number[]
  reason?: string | null
  expected_data_versions: Record<number, number>
}

export interface BatchReviewItem {
  record_id: number
  ok: boolean
  partition: string | null
  error: string | null
}

export interface BatchReviewResponse {
  batch_operation_id: string
  results: BatchReviewItem[]
}

/** Deep Link query 参数（D-062）。status=review 归一化为 needs_review。 */
export interface RecordListParams {
  partition?: RecordPartition | null
  q?: string | null
  field?: string | null
  value?: string | null
  source_type?: string | null
  extract_method?: string | null
  min_confidence?: number | null
  review_type?: string | null
  sort_by?: string | null
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}
