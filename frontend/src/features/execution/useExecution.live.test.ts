import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

import { useExecution } from './useExecution'

// 先定义 listeners，供 fakeSource.addEventListener 闭包引用（避免 use-before-define）。
const listeners: Record<string, (e: any) => void> = {}

const fakeSource = {
  close: vi.fn(),
  addEventListener: vi.fn((type: string, cb: (e: any) => void) => {
    listeners[type] = cb
  }),
}

vi.mock('./execution.api', () => ({
  getExecution: vi.fn(() => Promise.resolve({ task_id: 25, last_event_id: 9 })),
  getTimeline: vi.fn(() =>
    Promise.resolve({ task_id: 25, items: [], next_cursor: null, has_more: false }),
  ),
  getDag: vi.fn(() =>
    Promise.resolve({
      task_id: 25,
      plan_version: 1,
      spec_version: 1,
      validation_status: 'VALID',
      stage_status: {},
      nodes: [],
      edges: [],
    }),
  ),
  openExecutionTimelineStream: vi.fn(() => fakeSource),
  parseTimelineSseMessage: (data: string) => {
    try {
      return JSON.parse(data)
    } catch {
      return null
    }
  },
}))

function emitTimeline(eventId: number) {
  listeners.timeline?.({
    data: JSON.stringify({
      event_id: eventId,
      timestamp: '2026-08-21T12:00:00Z',
      categories: [],
      stage: 'fetch',
      summary: '抓取完成',
      status: 'COMPLETED',
      node_id: 'n3',
      node_type: 'fetch',
      run_id: 8,
      retry_count: 0,
    }),
  })
}

describe('useExecution live timeline stream', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('事件到达即追加并去重', async () => {
    const store = useExecution(ref(25))
    store.connectLive()
    await flushPromises()
    emitTimeline(10)
    emitTimeline(10)
    emitTimeline(11)
    await nextTick()
    expect(store.timeline.value.map((e) => e.event_id)).toEqual([10, 11])
  })

  it('burst 事件只触发一次节流 snapshot 刷新', async () => {
    const store = useExecution(ref(25))
    const refresh = vi.spyOn(store, 'refreshSnapshot')
    store.connectLive()
    await flushPromises()
    emitTimeline(10)
    emitTimeline(11)
    emitTimeline(12)
    await nextTick()
    vi.advanceTimersByTime(600) // 节流窗口结束
    await flushPromises()
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('reconnect→open 触发一次 reconcile 刷新', async () => {
    const store = useExecution(ref(25))
    const refresh = vi.spyOn(store, 'refreshSnapshot')
    store.connectLive()
    await flushPromises()
    ;(fakeSource as any).onerror?.()
    ;(fakeSource as any).onopen?.() // EventSource mock 触发 reconnecting→open
    await nextTick()
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('taskId 变化断开旧流并重置', async () => {
    const taskId = ref(25)
    const store = useExecution(taskId)
    store.connectLive()
    taskId.value = 26
    await nextTick()
    expect(fakeSource.close).toHaveBeenCalled()
    expect(store.live.value).toBe('idle')
  })
})
