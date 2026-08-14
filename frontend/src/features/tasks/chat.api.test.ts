import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient, AI_REQUEST_TIMEOUT_MS } from '@/app/api/client'
import { runUnderstanding } from '@/features/tasks/chat.api'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('runUnderstanding AI timeout + trigger contract', () => {
  it('uses the AI-specific bounded timeout, not the 10s CRUD default', async () => {
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValue({
        task_id: 1,
        status: 'SUCCEEDED',
        message: {},
        result: {},
        spec_draft: {},
      })
    await runUnderstanding(1)
    expect(postSpy).toHaveBeenCalledWith(
      '/tasks/1/understand',
      { trigger_source: 'AUTO_INITIAL' },
      { timeoutMs: AI_REQUEST_TIMEOUT_MS },
    )
  })

  it('sends USER_REUNDERSTAND when the user explicitly re-understands', async () => {
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValue({
        task_id: 1,
        status: 'SUCCEEDED',
        message: {},
        result: {},
        spec_draft: {},
      })
    await runUnderstanding(1, 'USER_REUNDERSTAND')
    expect(postSpy).toHaveBeenCalledWith(
      '/tasks/1/understand',
      { trigger_source: 'USER_REUNDERSTAND' },
      { timeoutMs: AI_REQUEST_TIMEOUT_MS },
    )
  })
})
