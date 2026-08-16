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
  | 'APPROVAL_APPROVED'
  | 'APPROVAL_REJECTED'
  | 'APPROVAL_EXPIRED'
  | 'APPROVAL_REVOKED'
  | 'APPROVAL_CONSUMED'
  | 'RECORD_APPROVED'
  | 'RECORD_REJECTED'
  | 'RECORD_EDITED'
  | 'RECORD_REEVALUATE_REQUESTED'
  | 'RECORD_APPROVED_BATCH'
  | 'RECORD_REJECTED_BATCH'
  | 'EXECUTION_PREFLIGHT_BLOCKED'
  | 'SOURCE_CANDIDATES_FOUND'
  | 'LINKS_DISCOVERED'
  | 'RUN_STARTED'
  | 'NODE_STARTED'
  | 'NODE_PROGRESS'
  | 'CHECKPOINT_COMMITTED'
  | 'NODE_COMPLETED'
  | 'NODE_BLOCKED'
  | 'NODE_FAILED'
  | 'RUN_COMPLETED'
  | 'RUN_PARTIALLY_COMPLETED'
  | 'RUN_FAILED'
  | 'RUN_CANCELLED'
  | 'FETCH_STARTED'
  | 'FETCH_STRATEGY_SELECTED'
  | 'BROWSER_ESCALATION'
  | 'CREDENTIAL_REQUIRED'
  | 'FETCH_COMPLETED'
  | 'FETCH_FAILED'
  | 'EXTRACTION_STARTED'
  | 'EXTRACTION_PROGRESS'
  | 'LLM_FALLBACK_USED'
  | 'RULE_PROMOTED'
  | 'EXTRACTION_COMPLETED'
  | 'EXTRACTION_FAILED'
  | 'NORMALIZE_COMPLETED'
  | 'VALIDATION_STARTED'
  | 'VALIDATION_PROGRESS'
  | 'DEDUPE_COMPLETED'
  | 'VALIDATION_COMPLETED'

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
