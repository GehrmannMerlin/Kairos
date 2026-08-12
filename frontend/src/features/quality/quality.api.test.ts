import { beforeEach, describe, expect, it, vi } from 'vitest'

import { buildDataLink } from '@/features/data/buildDataLink'
import { getQuality } from './quality.api'

vi.mock('@/app/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

import { apiClient } from '@/app/api/client'

const getMock = vi.mocked(apiClient.get)

describe('quality.api', () => {
  beforeEach(() => vi.clearAllMocks())

  it('getQuality hits /tasks/{id}/quality', async () => {
    getMock.mockResolvedValue({ task_id: 9 } as never)
    await getQuality('9')
    expect(getMock).toHaveBeenCalledWith('/tasks/9/quality')
  })
})

describe('buildDataLink', () => {
  it('serializes status=review deep link to M-13 data route', () => {
    const link = buildDataLink('9', { status: 'review', review_type: 'missing_required' })
    expect(link).toEqual({
      name: 'task-data',
      params: { taskId: '9' },
      query: { status: 'review', review_type: 'missing_required' },
    })
  })

  it('omits null fields and keeps min_confidence numeric', () => {
    const link = buildDataLink(9, { source_type: 'official_site', min_confidence: 0.6 })
    expect(link.query).toEqual({ source_type: 'official_site', min_confidence: '0.6' })
  })

  it('returns empty query for empty drilldown', () => {
    const link = buildDataLink('9', {})
    expect(link.query).toEqual({})
  })
})
