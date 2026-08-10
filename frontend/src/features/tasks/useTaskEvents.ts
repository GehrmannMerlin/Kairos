import { onBeforeUnmount, ref, type Ref } from 'vue'
import { openTaskEventStream, parseSseMessage, type TaskSseEvent } from './events.api'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

/** 统一 Task 事件订阅。断线自动重连（带 cursor），恢复后由调用方重新拉取 Task Snapshot。 */
export function useTaskEvents(taskId: Ref<string | number>) {
  const connection = ref<ConnectionStatus>('idle')
  const lastEventId = ref<number | undefined>(undefined)
  const latestEvent = ref<TaskSseEvent | null>(null)
  let source: EventSource | null = null

  function connect(): void {
    disconnect()
    connection.value = 'connecting'
    source = openTaskEventStream(taskId.value, lastEventId.value)
    source.onopen = () => {
      connection.value = 'open'
    }
    source.onmessage = (msg) => {
      const ev = parseSseMessage(msg.data)
      if (!ev) return
      lastEventId.value = ev.event_id
      latestEvent.value = ev
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
