import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { openTaskEventStream } from './events.api'

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
