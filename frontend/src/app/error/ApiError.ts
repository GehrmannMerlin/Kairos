/** Typed error raised by the API client for any non-successful response. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /** Stable machine-readable code from the backend `{ code, message }` body. */
  readonly code: string
  readonly cause?: unknown

  constructor(status: number, detail: string, code = '', cause?: unknown) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
    this.cause = cause
  }

  get isServerError(): boolean {
    return this.status >= 500
  }

  get isClientError(): boolean {
    return this.status >= 400 && this.status < 500
  }
}

/**
 * 客户端请求级错误（与后端错误 code 区分，前缀 CLIENT_ 避免命名冲突）。
 * 不得把「浏览器主动 timeout / 取消」误标成 Provider 网络故障。
 */
export class ClientTimeoutError extends ApiError {
  constructor(cause?: unknown) {
    super(0, '请求处理时间较长，服务器可能仍在处理中，正在确认结果…', 'CLIENT_TIMEOUT', cause)
  }
}

export class NetworkError extends ApiError {
  constructor(cause?: unknown) {
    super(0, '网络连接失败，请稍后重试。', 'CLIENT_NETWORK_ERROR', cause)
  }
}

export class RequestAbortedError extends ApiError {
  constructor(cause?: unknown) {
    super(0, '请求已取消', 'CLIENT_ABORTED', cause)
  }
}
