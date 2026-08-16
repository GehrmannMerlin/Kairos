import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { openTaskEventStream } from './events.api'
import { useTaskEvents } from './useTaskEvents'

class FakeEventSource {
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  private listeners = new Map<string, ((msg: MessageEvent) => void)[]>()

  addEventListener(type: string, cb: (msg: MessageEvent) => void): void {
    const list = this.listeners.get(type) ?? []
    list.push(cb)
    this.listeners.set(type, list)
  }
  emit(type: string, data: string): void {
    const cbs = this.listeners.get(type) ?? []
    for (const cb of cbs) cb({ data } as MessageEvent)
  }
}

describe('openTaskEventStream', () => {
  it('builds SSE url with cursor', () => {
    vi.stubGlobal(
      'EventSource',
      vi.fn().mockImplementation((url: string) => ({ url, close: vi.fn() })),
    )
    const taskId = ref(3)
    const es = openTaskEventStream(taskId.value, 7)
    expect((es as unknown as { url: string }).url).toContain('/api/events/tasks/3')
    expect((es as unknown as { url: string }).url).toContain('after_id=7')
    vi.unstubAllGlobals()
  })
})

describe('useTaskEvents', () => {
  it('consumes named SSE events via addEventListener (no onmessage dependency)', () => {
    let fake: FakeEventSource | null = null
    vi.stubGlobal(
      'EventSource',
      vi.fn().mockImplementation(() => {
        fake = new FakeEventSource()
        return fake
      }),
    )
    const taskId = ref(3)
    const store = useTaskEvents(taskId)
    store.connect()
    expect(fake).not.toBeNull()

    // 后端发命名事件 `event: TASK_PAUSED`，必须走 addEventListener 分支。
    fake!.emit(
      'TASK_PAUSED',
      JSON.stringify({
        event_id: 9,
        event_type: 'TASK_PAUSED',
        task_id: 3,
        run_id: null,
        occurred_at: '2026-08-10T00:00:00Z',
        payload: {},
      }),
    )
    expect(store.latestEvent.value?.event_type).toBe('TASK_PAUSED')
    expect(store.lastEventId.value).toBe(9)

    store.disconnect()
    vi.unstubAllGlobals()
  })

  it('accepts canonical events monotonically and reconciles once after reconnect', () => {
    let fake: FakeEventSource | null = null
    vi.stubGlobal(
      'EventSource',
      vi.fn().mockImplementation(() => {
        fake = new FakeEventSource()
        return fake
      }),
    )
    const store = useTaskEvents(ref(25))
    store.connect()

    const emit = (id: number) =>
      fake!.emit(
        'NODE_STARTED',
        JSON.stringify({
          event_id: id,
          event_type: 'NODE_STARTED',
          task_id: 25,
          run_id: 7,
          occurred_at: '2026-08-16T00:00:00Z',
          payload: { node_id: 'n-fetch' },
        }),
      )

    emit(18)
    emit(18)
    emit(17)
    expect(store.lastEventId.value).toBe(18)
    expect(store.latestEvent.value?.event_id).toBe(18)

    fake!.onerror?.()
    fake!.onopen?.()
    expect(store.connection.value).toBe('open')
    expect(store.reconcileVersion.value).toBe(1)

    store.disconnect()
    vi.unstubAllGlobals()
  })
})
