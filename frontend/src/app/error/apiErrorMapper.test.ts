import { describe, expect, it } from 'vitest'

import { ApiError } from '@/app/error/ApiError'
import { mapApiError } from '@/app/error/apiErrorMapper'

describe('mapApiError', () => {
  it('maps stable backend codes', () => {
    const cases: Array<[ApiError, string]> = [
      [new ApiError(401, '请先登录', 'AUTH_REQUIRED'), 'unauthenticated'],
      [new ApiError(401, '凭据错误', 'INVALID_CREDENTIALS'), 'unauthenticated'],
      [new ApiError(409, '需要配置 AI 模型', 'MODEL_NOT_CONFIGURED'), 'model_not_configured'],
      [
        new ApiError(409, '需要配置搜索服务', 'SEARCH_PROVIDER_NOT_CONFIGURED'),
        'search_provider_not_configured',
      ],
      [new ApiError(404, '资源不存在', 'NOT_FOUND'), 'not_found'],
      [new ApiError(409, '任务已被其他操作修改', 'STALE_VERSION'), 'conflict'],
      [new ApiError(409, '幂等冲突', 'IDEMPOTENCY_CONFLICT'), 'conflict'],
      [new ApiError(429, '操作过于频繁', 'RATE_LIMITED'), 'rate_limited'],
    ]
    for (const [err, kind] of cases) {
      expect(mapApiError(err).kind).toBe(kind)
      expect(mapApiError(err).message).toBe(err.detail)
    }
  })

  it('falls back by status when code is absent', () => {
    expect(mapApiError(new ApiError(503, '服务暂不可用')).kind).toBe('service_unavailable')
    expect(mapApiError(new ApiError(404, 'x')).kind).toBe('not_found')
    expect(mapApiError(new ApiError(409, 'x')).kind).toBe('conflict')
    expect(mapApiError(new ApiError(429, 'x')).kind).toBe('rate_limited')
  })

  it('maps network failure', () => {
    expect(mapApiError(new ApiError(0, '网络连接失败，请稍后重试。', 'CLIENT_NETWORK_ERROR')).kind).toBe(
      'network',
    )
  })

  it('maps generic errors to unknown', () => {
    expect(mapApiError(new Error('boom')).kind).toBe('unknown')
  })

  it('guides to /models when a model is required', () => {
    const mapped = mapApiError(new ApiError(409, 'x', 'MODEL_NOT_CONFIGURED'))
    expect(mapped.action).toBe('/models')
  })

  it('distinguishes client timeout from network error and abort', () => {
    expect(mapApiError(new ApiError(0, '请求处理时间较长', 'CLIENT_TIMEOUT')).kind).toBe(
      'client_timeout',
    )
    expect(mapApiError(new ApiError(0, '网络连接失败', 'CLIENT_NETWORK_ERROR')).kind).toBe(
      'network',
    )
    expect(mapApiError(new ApiError(0, '请求已取消', 'CLIENT_ABORTED')).kind).toBe(
      'request_aborted',
    )
  })

  it('keeps legacy status-0 errors mapped to network', () => {
    expect(mapApiError(new ApiError(0, 'legacy')).kind).toBe('network')
  })
})
