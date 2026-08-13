/** M-14 Quality Query API client（D-062）。只读诊断。 */

import { apiClient } from '@/app/api/client'

import type { QualityView } from './types'

export function getQuality(taskId: string | number): Promise<QualityView> {
  return apiClient.get<QualityView>(`/tasks/${taskId}/quality`)
}
