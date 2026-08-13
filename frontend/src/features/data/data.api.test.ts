import { beforeEach, describe, expect, it, vi } from 'vitest'

import { batchReview, queryRecords, reviewRecord } from './data.api'

vi.mock('@/app/api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}))

import { apiClient } from '@/app/api/client'

const getMock = vi.mocked(apiClient.get)
const postMock = vi.mocked(apiClient.post)

describe('data.api', () => {
  beforeEach(() => vi.clearAllMocks())

  it('queryRecords maps params to query string with partition', async () => {
    getMock.mockResolvedValue({
      task_id: 1,
      partition_counts: {},
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      dataset_version: null,
    })
    await queryRecords('9', { partition: 'needs_review', sort_order: 'desc' })
    const [url] = getMock.mock.calls[0]
    expect(String(url)).toContain('/tasks/9/records?')
    expect(String(url)).toContain('partition=needs_review')
    expect(String(url)).toContain('sort_order=desc')
  })

  it('reviewRecord posts action with expected_data_version', async () => {
    postMock.mockResolvedValue({ record: {} as never })
    await reviewRecord('9', 42, { action: 'approve', expected_data_version: 3 })
    expect(postMock).toHaveBeenCalledWith('/tasks/9/records/42/review', {
      action: 'approve',
      expected_data_version: 3,
    })
  })

  it('batchReview posts batch command', async () => {
    postMock.mockResolvedValue({ batch_operation_id: 'b1', results: [] })
    await batchReview('9', {
      action: 'approve',
      record_ids: [1, 2],
      expected_data_versions: { 1: 0, 2: 0 },
    })
    expect(postMock).toHaveBeenCalledWith(
      '/tasks/9/records/batch-review',
      expect.objectContaining({ record_ids: [1, 2] }),
    )
  })
})
