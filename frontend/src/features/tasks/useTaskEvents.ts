import { getCurrentInstance, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import {
  openTaskEventStream,
  parseSseMessage,
  type TaskEventType,
  type TaskSseEvent,
} from './events.api'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

// 后端 SSE 发送的是命名事件（`event: TASK_X`），EventSource 对命名事件不会触发
// `onmessage`，必须按事件类型注册 listener（SSE 规范）。keepalive `: ping` 注释行
// 不产生事件，天然忽略。
const _EVENT_TYPES: TaskEventType[] = [
  'TASK_STATE_CHANGED',
  'TASK_PAUSE_REQUESTED',
  'TASK_PAUSED',
  'TASK_RESUMED',
  'TASK_CANCEL_REQUESTED',
  'TASK_CANCELLED',
  'TASK_COMPLETED',
  'TASK_PARTIALLY_COMPLETED',
  'TASK_FAILED',
  'APPROVAL_REQUIRED',
  'APPROVAL_APPROVED',
  'APPROVAL_REJECTED',
  'APPROVAL_EXPIRED',
  'APPROVAL_REVOKED',
  'APPROVAL_CONSUMED',
  'RECORD_APPROVED',
  'RECORD_REJECTED',
  'RECORD_EDITED',
  'RECORD_REEVALUATE_REQUESTED',
  'RECORD_APPROVED_BATCH',
  'RECORD_REJECTED_BATCH',
  'EXECUTION_PREFLIGHT_BLOCKED',
  'SOURCE_CANDIDATES_FOUND',
  'LINKS_DISCOVERED',
  'RUN_STARTED',
  'NODE_STARTED',
  'NODE_PROGRESS',
  'CHECKPOINT_COMMITTED',
  'NODE_COMPLETED',
  'NODE_BLOCKED',
  'NODE_FAILED',
  'RUN_COMPLETED',
  'RUN_PARTIALLY_COMPLETED',
  'RUN_FAILED',
  'RUN_CANCELLED',
  'FETCH_STARTED',
  'FETCH_STRATEGY_SELECTED',
  'BROWSER_ESCALATION',
  'CREDENTIAL_REQUIRED',
  'FETCH_COMPLETED',
  'FETCH_FAILED',
  'EXTRACTION_STARTED',
  'EXTRACTION_PROGRESS',
  'LLM_FALLBACK_USED',
  'RULE_PROMOTED',
  'EXTRACTION_COMPLETED',
  'EXTRACTION_FAILED',
  'NORMALIZE_COMPLETED',
  'VALIDATION_STARTED',
  'VALIDATION_PROGRESS',
  'DEDUPE_COMPLETED',
  'VALIDATION_COMPLETED',
]

/** 统一 Task 事件订阅。断线自动重连（带 cursor），恢复后由调用方重新拉取 Task Snapshot。 */
export function useTaskEvents(taskId: Ref<string | number>) {
  const connection = ref<ConnectionStatus>('idle')
  const lastEventId = ref<number | undefined>(undefined)
  const latestEvent = ref<TaskSseEvent | null>(null)
  const reconcileVersion = ref(0)
  let source: EventSource | null = null

  function handleEvent(msg: MessageEvent): void {
    const ev = parseSseMessage(String(msg.data))
    if (!ev) return
    if (ev.event_id <= (lastEventId.value ?? 0)) return
    lastEventId.value = ev.event_id
    latestEvent.value = ev
  }

  function connect(): void {
    disconnect()
    connection.value = 'connecting'
    source = openTaskEventStream(taskId.value, lastEventId.value)
    source.onopen = () => {
      if (connection.value === 'reconnecting') reconcileVersion.value += 1
      connection.value = 'open'
    }
    for (const type of _EVENT_TYPES) {
      source.addEventListener(type, handleEvent)
    }
    source.onerror = () => {
      // EventSource 自动重连；Last-Event-ID 由浏览器自动携带
      connection.value = 'reconnecting'
    }
  }

  function disconnect(): void {
    source?.close()
    source = null
    connection.value = 'closed'
  }

  watch(taskId, () => {
    const shouldReconnect = source !== null
    disconnect()
    lastEventId.value = undefined
    latestEvent.value = null
    reconcileVersion.value = 0
    if (shouldReconnect) connect()
  })

  if (getCurrentInstance()) onBeforeUnmount(disconnect)

  return { connection, lastEventId, latestEvent, reconcileVersion, connect, disconnect }
}
