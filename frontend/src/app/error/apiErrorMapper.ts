import type { ApiError } from '@/app/error/ApiError'

/** 统一全局 API 错误语义。组件不应各自复制 `if (status === 401) ...` 判断。 */
export type ApiErrorKind =
  | 'unauthenticated'
  | 'model_not_configured'
  | 'search_provider_not_configured'
  | 'provider'
  | 'not_found'
  | 'conflict'
  | 'rate_limited'
  | 'service_unavailable'
  | 'network'
  | 'unknown'

export interface MappedApiError {
  kind: ApiErrorKind
  message: string
  /** 可执行跳转路径（如 '/models'）；否则 undefined。 */
  action?: string
}

const CODE_KIND: Record<string, ApiErrorKind> = {
  AUTH_REQUIRED: 'unauthenticated',
  INVALID_CREDENTIALS: 'unauthenticated',
  MODEL_NOT_CONFIGURED: 'model_not_configured',
  SEARCH_PROVIDER_NOT_CONFIGURED: 'search_provider_not_configured',
  NOT_FOUND: 'not_found',
  STALE_VERSION: 'conflict',
  IDEMPOTENCY_CONFLICT: 'conflict',
  ILLEGAL_TRANSITION: 'conflict',
  EMAIL_TAKEN: 'conflict',
  RATE_LIMITED: 'rate_limited',
  // M-03 provider call taxonomy (reused, not a second set of strings).
  AUTH_FAILED: 'provider',
  MODEL_NOT_FOUND: 'provider',
  PROVIDER_INFERENCE_ERROR: 'provider',
  NETWORK_ERROR: 'provider',
}

const STATUS_KIND: Record<number, ApiErrorKind> = {
  401: 'unauthenticated',
  404: 'not_found',
  409: 'conflict',
  429: 'rate_limited',
  503: 'service_unavailable',
}

function isApiError(value: unknown): value is ApiError {
  return value instanceof Error && typeof (value as ApiError).status === 'number'
}

function actionFor(kind: ApiErrorKind): string | undefined {
  if (kind === 'model_not_configured' || kind === 'search_provider_not_configured') {
    return '/models'
  }
  if (kind === 'unauthenticated') {
    return '/login'
  }
  return undefined
}

/** 优先按后端稳定 code 映射，其次按 HTTP status，最后按网络失败（status 0）。 */
export function mapApiError(error: unknown): MappedApiError {
  if (!isApiError(error)) {
    return { kind: 'unknown', message: error instanceof Error ? error.message : String(error) }
  }

  const byCode = error.code ? CODE_KIND[error.code] : undefined
  if (byCode) {
    return { kind: byCode, message: error.detail, action: actionFor(byCode) }
  }
  if (error.status >= 500) {
    return { kind: 'service_unavailable', message: error.detail, action: undefined }
  }
  const byStatus = STATUS_KIND[error.status]
  if (byStatus) {
    return { kind: byStatus, message: error.detail, action: actionFor(byStatus) }
  }
  if (error.status === 0) {
    return { kind: 'network', message: error.detail, action: undefined }
  }
  return { kind: 'unknown', message: error.detail }
}
