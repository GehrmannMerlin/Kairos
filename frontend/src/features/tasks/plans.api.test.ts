import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient, AI_REQUEST_TIMEOUT_MS } from '@/app/api/client'
import { generatePlan } from '@/features/tasks/plans.api'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('generatePlan AI timeout contract', () => {
  it('uses the AI-specific bounded timeout (synchronous model call)', async () => {
    const postSpy = vi
      .spyOn(apiClient, 'post')
      .mockResolvedValue({ task_id: 1, plan_version: 1, validation_status: 'VALID', node_count: 3 })
    await generatePlan(1, { spec_version: 1, expected_version: 2 })
    expect(postSpy).toHaveBeenCalledWith('/tasks/1/plan', { spec_version: 1, expected_version: 2 }, {
      timeoutMs: AI_REQUEST_TIMEOUT_MS,
    })
  })
})
