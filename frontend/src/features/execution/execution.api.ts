/** M-14 Execution API client（D-055/D-063）。只读。 */

import { apiClient } from '@/app/api/client'

import type {
  DagView,
  ExecutionView,
  NodeDetailDto,
  TimelineCategory,
  TimelineEvent,
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

export function getTimeline(taskId: string | number, query: TimelineQuery): Promise<TimelinePage> {
  const qs = new URLSearchParams()
  if (query.category) qs.set('category', query.category)
  if (query.afterId != null) qs.set('after_id', String(query.afterId))
  qs.set('limit', String(query.limit ?? 50))
  return apiClient.get<TimelinePage>(`/tasks/${taskId}/execution/timeline?${qs}`)
}

export function getDag(taskId: string | number): Promise<DagView> {
  return apiClient.get<DagView>(`/tasks/${taskId}/execution/dag`)
}

export function getNodeDetail(taskId: string | number, nodeId: string): Promise<NodeDetailDto> {
  return apiClient.get<NodeDetailDto>(`/tasks/${taskId}/execution/nodes/${nodeId}`)
}

const SSE_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export interface TimelineStreamOptions {
  lastEventId?: number
}

/** 打开 timeline SSE 流。事件格式：event: timeline, data: TimelineEvent JSON。 */
export function openExecutionTimelineStream(
  taskId: string | number,
  options: TimelineStreamOptions = {},
): EventSource {
  const url = new URL(
    `${SSE_BASE_URL}/tasks/${taskId}/execution/timeline/stream`,
    window.location.origin,
  )
  if (options.lastEventId != null) url.searchParams.set('after_id', String(options.lastEventId))
  return new EventSource(url.toString())
}

export function parseTimelineSseMessage(data: string): TimelineEvent | null {
  try {
    return JSON.parse(data) as TimelineEvent
  } catch {
    return null
  }
}
