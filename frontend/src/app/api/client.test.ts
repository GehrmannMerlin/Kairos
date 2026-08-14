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

  it('throws NetworkError (CLIENT_NETWORK_ERROR) for real network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')))
    const client = new ApiClient({ baseUrl: '/api' })
    await expect(client.get('/health/live')).rejects.toMatchObject({
      name: 'ApiError',
      status: 0,
      code: 'CLIENT_NETWORK_ERROR',
    })
  })
})

describe('ApiClient request-level timeout contract', () => {
  /**
   * fetch mock that rejects only when the AbortSignal fires — lets the test
   * drive timing deterministically with fake timers (no real 10s/60s sleeps).
   */
  function fetchThatHonorsAbort(): void {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        (_input, init?: { signal?: AbortSignal }) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('The operation was aborted.', 'AbortError')),
            )
          }),
      ),
    )
  }

  it('keeps the 10s CRUD default timeout and allows a per-request override', async () => {
    vi.useFakeTimers()
    try {
      fetchThatHonorsAbort()
      const client = new ApiClient({ baseUrl: '/api' })

      // Default: aborts at 10s → CLIENT_TIMEOUT. (Assertion attached before the
      // timer fires so the rejection is never left unhandled.)
      const defaultReq = client.get('/health/live')
      const defaultAssertion = expect(defaultReq).rejects.toMatchObject({
        code: 'CLIENT_TIMEOUT',
      })
      await vi.advanceTimersByTimeAsync(10_000)
      await defaultAssertion

      // Per-request 60s override: NOT aborted at 10s, aborts at 60s.
      const longReq = client.get('/health/live', { timeoutMs: 60_000 })
      const longAssertion = expect(longReq).rejects.toMatchObject({ code: 'CLIENT_TIMEOUT' })
      let longSettled = false
      longReq.catch(() => {
        longSettled = true
      })
      await vi.advanceTimersByTimeAsync(10_000)
      expect(longSettled).toBe(false)
      await vi.advanceTimersByTimeAsync(50_000)
      await longAssertion
    } finally {
      vi.useRealTimers()
    }
  })

  it('a 15s model response succeeds under a 60s AI timeout (not aborted by 10s)', async () => {
    vi.useFakeTimers()
    try {
      let resolveFetch!: (r: Response) => void
      vi.stubGlobal(
        'fetch',
        vi.fn().mockImplementation(
          (_input, init?: { signal?: AbortSignal }) =>
            new Promise<Response>((resolve, reject) => {
              resolveFetch = resolve
              init?.signal?.addEventListener('abort', () =>
                reject(new DOMException('aborted', 'AbortError')),
              )
            }),
        ),
      )
      const client = new ApiClient({ baseUrl: '/api' })
      const promise = client.get<{ status: string }>('/slow', { timeoutMs: 60_000 })
      await vi.advanceTimersByTimeAsync(15_000)
      resolveFetch({ ok: true, status: 200, json: async () => ({ status: 'ok' }) } as Response)
      await expect(promise).resolves.toEqual({ status: 'ok' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('maps an external abort to CLIENT_ABORTED (not a network failure)', async () => {
    fetchThatHonorsAbort()
    const controller = new AbortController()
    const client = new ApiClient({ baseUrl: '/api' })
    const promise = client.get('/health/live', { signal: controller.signal })
    controller.abort()
    await expect(promise).rejects.toMatchObject({ code: 'CLIENT_ABORTED' })
  })

  it('does not confuse CLIENT_TIMEOUT with NETWORK_ERROR', async () => {
    vi.useFakeTimers()
    try {
      fetchThatHonorsAbort()
      const client = new ApiClient({ baseUrl: '/api' })
      const timedOut = client.get('/x', { timeoutMs: 50 })
      const assertion = expect(timedOut).rejects.toMatchObject({
        code: 'CLIENT_TIMEOUT',
        detail: '请求处理时间较长，服务器可能仍在处理中，正在确认结果…',
      })
      await vi.advanceTimersByTimeAsync(50)
      await assertion
    } finally {
      vi.useRealTimers()
    }
  })
})
