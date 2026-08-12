/** M-14 Execution API client（D-055/D-063）。只读。 */

import { apiClient } from '@/app/api/client'

import type {
  DagView,
  ExecutionView,
  NodeDetailDto,
  TimelineCategory,
  TimelinePage,
} from './types'

export interface TimelineQuery {
  category?: TimelineCategory
  afterId?: number
  limit?: number
}

export function getExecution(taskId: string | number): Promise<ExecutionView> {
  return apiClient.get<ExecutionView>(`/tasks/${taskId}/execution`)
}

export function getTimeline(
  taskId: string | number,
  query: TimelineQuery,
): Promise<TimelinePage> {
  const qs = new URLSearchParams()
  if (query.category) qs.set('category', query.category)
  if (query.afterId != null) qs.set('after_id', String(query.afterId))
  qs.set('limit', String(query.limit ?? 50))
  return apiClient.get<TimelinePage>(`/tasks/${taskId}/execution/timeline?${qs}`)
}

export function getDag(taskId: string | number): Promise<DagView> {
  return apiClient.get<DagView>(`/tasks/${taskId}/execution/dag`)
}

export function getNodeDetail(
  taskId: string | number,
  nodeId: string,
): Promise<NodeDetailDto> {
  return apiClient.get<NodeDetailDto>(`/tasks/${taskId}/execution/nodes/${nodeId}`)
}
