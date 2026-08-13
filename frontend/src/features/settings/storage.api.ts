/** M-15 设置 → 存储与数据 API（D-052/D-072）：只读摘要 + retention dry-run 预览。 */

import { apiClient } from '@/app/api/client'

export interface StorageSummaryDto {
  task_count: number
  record_count: number
  evidence_count: number
  artifact_count: number
  snapshot_bytes: number
  artifact_bytes: number
  retention_days: number
}

export interface CleanupPreviewDto {
  policy_version: string
  retention_days: number
  dry_run: boolean
  scanned: number
  eligible: number
  protected: number
  deleted: number
  failed: number
  bytes_freed: number
  started_at: string
  completed_at: string
}

export function getStorageSummary(): Promise<StorageSummaryDto> {
  return apiClient.get<StorageSummaryDto>('/settings/storage-summary')
}

export function postCleanupPreview(): Promise<CleanupPreviewDto> {
  return apiClient.post<CleanupPreviewDto>('/settings/storage/cleanup-preview')
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
