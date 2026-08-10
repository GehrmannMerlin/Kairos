/** SSE 任务事件（对应后端 /api/events/tasks/{id}）。SSE 不是事实源，断线重连后
 *  前端仍以 Task Query 为准，SSE 只负责增量提醒。 */
export type TaskEventType =
  | 'TASK_STATE_CHANGED'
  | 'TASK_PAUSE_REQUESTED'
  | 'TASK_PAUSED'
  | 'TASK_RESUMED'
  | 'TASK_CANCEL_REQUESTED'
  | 'TASK_CANCELLED'
  | 'TASK_COMPLETED'
  | 'TASK_PARTIALLY_COMPLETED'
  | 'TASK_FAILED'
  | 'APPROVAL_REQUIRED'

export interface TaskSseEvent {
  event_id: number
  event_type: TaskEventType
  task_id: number
  run_id: number | null
  occurred_at: string
  payload: Record<string, unknown>
}

const DEFAULT_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export function openTaskEventStream(taskId: string | number, lastEventId?: number): EventSource {
  const url = new URL(`${DEFAULT_BASE_URL}/events/tasks/${taskId}`, window.location.origin)
  if (lastEventId) url.searchParams.set('after_id', String(lastEventId))
  return new EventSource(url.toString())
}

export function parseSseMessage(raw: string): TaskSseEvent | null {
  try {
    return JSON.parse(raw) as TaskSseEvent
  } catch {
    return null
  }
}
