import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/app/api/client'
import { generatePlan, getPlanSummary, startPlan } from '@/features/tasks/plans.api'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('plan API lifecycle contract', () => {
  it('disables only the browser timeout for the full plan lifecycle', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({
      task_id: 1,
      plan_version: 1,
      validation_status: 'VALID',
      node_count: 3,
      run_id: 9,
      workflow_id: 'task-workflow-1',
      run_state: 'pending',
      start_recoverable: false,
      validator_issues: [],
    })
    await generatePlan(1, { spec_version: 1, expected_version: 2 })
    expect(postSpy).toHaveBeenCalledWith(
      '/tasks/1/plan',
      { spec_version: 1, expected_version: 2 },
      { timeoutMs: null },
    )
  })

  it('keeps ordinary plan reads on the API client default timeout', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({})

    await getPlanSummary(1, 2)

    expect(getSpy).toHaveBeenCalledWith('/tasks/1/plans/2')
  })

  it('starts a persisted plan without submitting generation again', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({})

    await startPlan(1, 2)

    expect(postSpy).toHaveBeenCalledWith('/tasks/1/plans/2/start', undefined, undefined)
  })
})
