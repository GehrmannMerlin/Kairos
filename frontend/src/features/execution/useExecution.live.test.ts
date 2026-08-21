import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

import { getDag, getTimeline } from './execution.api'
import type { TimelineEvent } from './types'
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

function buildEvent(eventId: number, overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    event_id: eventId,
    timestamp: '2026-08-21T12:00:00Z',
    categories: [],
    stage: 'fetch',
    summary: `事件 ${eventId}`,
    status: 'COMPLETED',
    error_code: null,
    run_id: 8,
    node_run_id: null,
    node_id: 'n3',
    retry_count: 0,
    tool: null,
    model: null,
    duration_ms: null,
    tokens_in: null,
    tokens_out: null,
    evidence_refs: [],
    trace_ref: null,
    ...overrides,
  }
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

  it('burst 事件只触发一次节流 overview 刷新（不重置 timeline）', async () => {
    const store = useExecution(ref(25))
    const refresh = vi.spyOn(store, 'refreshLiveOverview')
    store.connectLive()
    await flushPromises()
    emitTimeline(10)
    emitTimeline(11)
    emitTimeline(12)
    await nextTick()
    expect(store.timeline.value.map((e) => e.event_id)).toEqual([10, 11, 12])
    vi.advanceTimersByTime(600) // 节流窗口结束
    await flushPromises()
    expect(refresh).toHaveBeenCalledTimes(1)
    // 轻量刷新不重置 timeline，流式增量保留。
    expect(store.timeline.value.map((e) => e.event_id)).toEqual([10, 11, 12])
  })

  it('reconnect→open 恰好触发一次 reconcile 刷新并递增版本', async () => {
    const store = useExecution(ref(25))
    const refresh = vi.spyOn(store, 'refreshSnapshot')
    store.connectLive()
    await flushPromises()
    ;(fakeSource as any).onerror?.()
    ;(fakeSource as any).onopen?.() // EventSource mock 触发 reconnecting→open
    await nextTick()
    // 单一 owner（useExecution.onopen）触发 reconcile；视图不再重复刷新。
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(store.reconcileVersion.value).toBe(1)
  })

  it('reconcile 刷新尾部保留：流已追加的高位事件不被分页首屏覆盖', async () => {
    const store = useExecution(ref(25))
    const page1 = Array.from({ length: 50 }, (_, i) => buildEvent(i + 1))
    const highTail = Array.from({ length: 10 }, (_, i) => buildEvent(51 + i))
    // 流已把事件 51-60 追加进 timeline，而服务端首屏分页只回 1-50。
    store.timeline.value = [...page1, ...highTail]
    vi.mocked(getTimeline).mockResolvedValueOnce({
      task_id: 25,
      items: page1,
      next_cursor: null,
      has_more: false,
    })
    await store.refreshSnapshot()
    expect(store.timeline.value.map((e) => e.event_id)).toEqual(
      Array.from({ length: 60 }, (_, i) => i + 1),
    )
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

  it('dag 模式且 DAG 已加载时，live 事件节流刷新重新拉取 DAG', async () => {
    const store = useExecution(ref(25))
    store.viewMode.value = 'dag'
    store.dag.value = {
      task_id: 25,
      plan_version: 1,
      spec_version: 1,
      validation_status: 'VALID',
      stage_status: {},
      nodes: [],
      edges: [],
    }
    store.connectLive()
    await flushPromises()
    expect(getDag).toHaveBeenCalledTimes(0)
    emitTimeline(10)
    vi.advanceTimersByTime(600)
    await flushPromises()
    expect(getDag).toHaveBeenCalledTimes(1)
  })

  it('stage 模式且 DAG 已加载时，live 事件节流刷新不重复拉取 DAG', async () => {
    const store = useExecution(ref(25))
    store.dag.value = {
      task_id: 25,
      plan_version: 1,
      spec_version: 1,
      validation_status: 'VALID',
      stage_status: {},
      nodes: [],
      edges: [],
    }
    store.connectLive()
    await flushPromises()
    emitTimeline(10)
    vi.advanceTimersByTime(600)
    await flushPromises()
    expect(getDag).toHaveBeenCalledTimes(0)
  })
})
