/** M-13 数据页 SSE 增量刷新：监听 record.* 事件（D-040）。 */

import { watch, type Ref } from 'vue'

import { useTaskEvents } from '@/features/tasks/useTaskEvents'

const RECORD_EVENTS = new Set([
  'RECORD_APPROVED',
  'RECORD_REJECTED',
  'RECORD_EDITED',
  'RECORD_REEVALUATE_REQUESTED',
  'RECORD_APPROVED_BATCH',
  'RECORD_REJECTED_BATCH',
])

/** record.* SSE 事件触发数据页重新拉取（事件非事实源，刷新以 Query 为准）。 */
export function useRecordEvents(taskId: Ref<string | number>, onRecordEvent: () => void): void {
  const { connect, latestEvent } = useTaskEvents(taskId)
  connect()
  watch(latestEvent, (ev) => {
    if (ev && RECORD_EVENTS.has(ev.event_type)) onRecordEvent()
  })
}
