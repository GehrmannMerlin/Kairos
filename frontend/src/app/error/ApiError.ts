/** Typed error raised by the API client for any non-successful response. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly cause?: unknown

  constructor(status: number, detail: string, cause?: unknown) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.cause = cause
  }

  get isServerError(): boolean {
    return this.status >= 500
  }

  get isClientError(): boolean {
    return this.status >= 400 && this.status < 500
  }
}
