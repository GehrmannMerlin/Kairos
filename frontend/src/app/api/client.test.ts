import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiClient } from '@/app/api/client'
import { ApiError } from '@/app/error/ApiError'

function mockFetchOnce(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as Response),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ApiClient', () => {
  it('returns parsed JSON on success', async () => {
    mockFetchOnce(200, { status: 'ok' })
    const client = new ApiClient({ baseUrl: '/api' })
    await expect(client.get('/health/live')).resolves.toEqual({ status: 'ok' })
  })

  it('throws ApiError with detail on non-2xx', async () => {
    mockFetchOnce(503, { detail: 'degraded' })
    const client = new ApiClient({ baseUrl: '/api' })
    const promise = client.get('/health/ready')
    await expect(promise).rejects.toBeInstanceOf(ApiError)
    await expect(promise).rejects.toMatchObject({ status: 503, detail: 'degraded' })
  })

  it('throws ApiError for network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')))
    const client = new ApiClient({ baseUrl: '/api', timeoutMs: 100 })
    await expect(client.get('/health/live')).rejects.toMatchObject({
      name: 'ApiError',
      status: 0,
    })
  })
})
