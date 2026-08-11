import { onBeforeUnmount, ref, type Ref } from 'vue'
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
]

/** 统一 Task 事件订阅。断线自动重连（带 cursor），恢复后由调用方重新拉取 Task Snapshot。 */
export function useTaskEvents(taskId: Ref<string | number>) {
  const connection = ref<ConnectionStatus>('idle')
  const lastEventId = ref<number | undefined>(undefined)
  const latestEvent = ref<TaskSseEvent | null>(null)
  let source: EventSource | null = null

  function handleEvent(msg: MessageEvent): void {
    const ev = parseSseMessage(String(msg.data))
    if (!ev) return
    lastEventId.value = ev.event_id
    latestEvent.value = ev
  }

  function connect(): void {
    disconnect()
    connection.value = 'connecting'
    source = openTaskEventStream(taskId.value, lastEventId.value)
    source.onopen = () => {
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

  onBeforeUnmount(disconnect)

  return { connection, lastEventId, latestEvent, connect, disconnect }
}
