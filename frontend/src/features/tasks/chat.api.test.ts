import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient, AI_REQUEST_TIMEOUT_MS } from '@/app/api/client'
import { runUnderstanding } from '@/features/tasks/chat.api'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('runUnderstanding AI timeout contract', () => {
  it('uses the AI-specific bounded timeout, not the 10s CRUD default', async () => {
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValue({ task_id: 1, message: {}, result: {}, spec_draft: {} })
    await runUnderstanding(1)
    expect(postSpy).toHaveBeenCalledWith('/tasks/1/understand', undefined, {
      timeoutMs: AI_REQUEST_TIMEOUT_MS,
    })
  })
})
