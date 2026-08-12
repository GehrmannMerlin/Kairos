/** M-13 数据工作区 API client（Records Query / Review / Batch Review）。 */

import { apiClient } from '@/app/api/client'

import type {
  BatchReviewCommand,
  BatchReviewResponse,
  RecordDetailView,
  RecordListParams,
  RecordListResponse,
  RecordReviewCommand,
  RecordReviewResponse,
} from './types'

function toQuery(params: RecordListParams): string {
  const qs = new URLSearchParams()
  if (params.partition) qs.set('partition', params.partition)
  if (params.q) qs.set('q', params.q)
  if (params.field) qs.set('field', params.field)
  if (params.value) qs.set('value', params.value)
  if (params.source_type) qs.set('source_type', params.source_type)
  if (params.extract_method) qs.set('extract_method', params.extract_method)
  if (params.min_confidence != null) qs.set('min_confidence', String(params.min_confidence))
  if (params.review_type) qs.set('review_type', params.review_type)
  if (params.sort_by) qs.set('sort_by', params.sort_by)
  qs.set('sort_order', params.sort_order ?? 'asc')
  qs.set('page', String(params.page ?? 1))
  qs.set('page_size', String(params.page_size ?? 20))
  return qs.toString()
}

export function queryRecords(
  taskId: string | number,
  params: RecordListParams,
): Promise<RecordListResponse> {
  return apiClient.get<RecordListResponse>(`/tasks/${taskId}/records?${toQuery(params)}`)
}

export function getRecordDetail(
  taskId: string | number,
  recordId: number,
): Promise<RecordDetailView> {
  return apiClient.get<RecordDetailView>(`/tasks/${taskId}/records/${recordId}`)
}

export function reviewRecord(
  taskId: string | number,
  recordId: number,
  cmd: RecordReviewCommand,
): Promise<RecordReviewResponse> {
  return apiClient.post<RecordReviewResponse>(`/tasks/${taskId}/records/${recordId}/review`, cmd)
}

export function batchReview(
  taskId: string | number,
  cmd: BatchReviewCommand,
): Promise<BatchReviewResponse> {
  return apiClient.post<BatchReviewResponse>(`/tasks/${taskId}/records/batch-review`, cmd)
}
