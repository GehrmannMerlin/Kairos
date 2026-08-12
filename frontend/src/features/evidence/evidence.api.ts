/** M-14 Evidence API client（D-056/D-064）。
 *
 * 内容通过 raw fetch 读取历史存储字节（JSON-free），绝不请求 source_url。
 */

import { apiClient } from '@/app/api/client'

import type { EvidenceContent, EvidenceView } from './types'

export function getEvidence(taskId: string | number, snapshotId: string | number): Promise<EvidenceView> {
  return apiClient.get<EvidenceView>(`/tasks/${taskId}/evidence/${snapshotId}`)
}

/** 读取已保存的快照对象字节。downloadUrl 来自后端 EvidenceView（owner-safe）。 */
export async function fetchEvidenceContent(downloadUrl: string): Promise<EvidenceContent> {
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  const resp = await fetch(`${base}${downloadUrl}`, { credentials: 'same-origin' })
  if (!resp.ok) {
    throw new Error(`加载证据内容失败 (${resp.status})`)
  }
  const contentType = resp.headers.get('content-type') ?? ''
  if (contentType.startsWith('image/')) {
    const blob = await resp.blob()
    return {
      text: '',
      contentType,
      isImage: true,
      imageUrl: URL.createObjectURL(blob),
    }
  }
  return { text: await resp.text(), contentType, isImage: false }
}
