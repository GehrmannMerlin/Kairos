import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getDag, getExecution, getNodeDetail, getTimeline } from './execution.api'

vi.mock('@/app/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

import { apiClient } from '@/app/api/client'

const getMock = vi.mocked(apiClient.get)

describe('execution.api', () => {
  beforeEach(() => vi.clearAllMocks())

  it('getExecution / getDag / getNodeDetail hit their routes', async () => {
    getMock.mockResolvedValue({} as never)
    await getExecution('9')
    expect(getMock).toHaveBeenCalledWith('/tasks/9/execution')
    await getDag('9')
    expect(getMock).toHaveBeenCalledWith('/tasks/9/execution/dag')
    await getNodeDetail('9', 'n-fetch')
    expect(getMock).toHaveBeenCalledWith('/tasks/9/execution/nodes/n-fetch')
  })

  it('getTimeline serializes category/after_id/limit', async () => {
    getMock.mockResolvedValue({ items: [] } as never)
    await getTimeline('9', { category: 'error', afterId: 42, limit: 20 })
    const [url] = getMock.mock.calls[0]
    expect(String(url)).toContain('/tasks/9/execution/timeline?')
    expect(String(url)).toContain('category=error')
    expect(String(url)).toContain('after_id=42')
    expect(String(url)).toContain('limit=20')
  })
})
