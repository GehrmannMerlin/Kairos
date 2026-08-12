/** M-15 Artifact API client：幂等导出 + 下载。 */

import { apiClient } from '@/app/api/client'

import type { ArtifactRef, ArtifactView, CompletionCardView, ExportRequest } from './types'

export function exportArtifact(
  taskId: string | number,
  request: ExportRequest,
): Promise<ArtifactRef> {
  return apiClient.post<ArtifactRef>(`/tasks/${taskId}/artifacts/export`, request)
}

export function listArtifacts(taskId: string | number): Promise<ArtifactView[]> {
  return apiClient.get<ArtifactView[]>(`/tasks/${taskId}/artifacts`)
}

/** 下载走普通 <a href> 触发浏览器下载；API 已带 session cookie + Content-Disposition。 */
export function artifactDownloadUrl(taskId: string | number, artifactId: number): string {
  return `/api/tasks/${taskId}/artifacts/${artifactId}/download`
}

export function getCompletion(taskId: string | number): Promise<CompletionCardView> {
  return apiClient.get<CompletionCardView>(`/tasks/${taskId}/completion`)
}
