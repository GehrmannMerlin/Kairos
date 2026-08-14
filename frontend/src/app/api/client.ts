import {
  ApiError,
  ClientTimeoutError,
  NetworkError,
  RequestAbortedError,
} from '@/app/error/ApiError'

export interface ApiClientOptions {
  /** Base path of the API, e.g. `/api` (same-origin behind a reverse proxy). */
  baseUrl: string
  timeoutMs?: number
}

/**
 * Per-request overrides.
 * - `undefined`: use the client's default timeout.
 * - `number`: this request uses the given timeout (0 disables the timer for tests).
 * - `null`: no frontend automatic timeout — the request still honors an external
 *   `signal`; the server's bounded provider timeout is the real ceiling.
 */
export interface RequestOptions {
  signal?: AbortSignal
  timeoutMs?: number | null
}

/** 普通 CRUD 请求默认超时（保持既有行为）。 */
export const DEFAULT_TIMEOUT_MS = 10_000
/** Provider Probe / 目录 / 连接测试：单次真实 provider 请求，有界 AI 超时。 */
export const PROBE_REQUEST_TIMEOUT_MS = 45_000
/** 同步模型调用（Goal Understanding / Plan 生成）：backend 单次 30s 窗口 + 校验重试余量。 */
export const AI_REQUEST_TIMEOUT_MS = 60_000

interface ApiErrorBody {
  detail?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/**
 * Thin JSON fetch wrapper. Later modules add auth headers, SSE, and command/query
 * DTOs on top of this single entry point.
 *
 * 超时契约：默认 CRUD 10s；`RequestOptions.timeoutMs` 可对单个请求覆盖（模型调用、
 * Provider Probe 使用各自明确的有界超时）。浏览器主动 abort 不能伪装成网络/Provider
 * 故障——超时 → CLIENT_TIMEOUT、外部取消 → CLIENT_ABORTED、真实网络失败 → NETWORK_ERROR。
 */
export class ApiClient {
  private readonly baseUrl: string
  private readonly timeoutMs: number

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, '')
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  }

  async get<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('GET', path, undefined, options)
  }

  async post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('POST', path, body, options)
  }

  async patch<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('PATCH', path, body, options)
  }

  async put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('PUT', path, body, options)
  }

  async delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('DELETE', path, undefined, options)
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    options: RequestOptions = {},
  ): Promise<T> {
    const { signal: externalSignal, timeoutMs = this.timeoutMs } = options
    const controller = new AbortController()
    let timedOut = false
    const timer =
      timeoutMs !== null && timeoutMs > 0
        ? setTimeout(() => {
            timedOut = true
            controller.abort()
          }, timeoutMs)
        : undefined
    const abortFromOutside = () => controller.abort()

    externalSignal?.addEventListener('abort', abortFromOutside, { once: true })
    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      })

      if (!response.ok) {
        throw await this.toApiError(response)
      }
      if (response.status === 204) {
        return undefined as T
      }
      return (await response.json()) as T
    } catch (error) {
      if (error instanceof ApiError) {
        throw error
      }
      // 先区分「本次是否由本客户端的超时计时器主动 abort」；否则外部 signal 取消；
      // 都不是才视为真实网络失败。超时绝不映射成 Provider/网络故障。
      if (timedOut) {
        throw new ClientTimeoutError(error)
      }
      if (externalSignal?.aborted) {
        throw new RequestAbortedError(error)
      }
      throw new NetworkError(error)
    } finally {
      if (timer !== undefined) {
        clearTimeout(timer)
      }
      externalSignal?.removeEventListener('abort', abortFromOutside)
    }
  }

  private async toApiError(response: Response): Promise<ApiError> {
    let detail: string = `请求失败 (${response.status})`
    let code = ''
    try {
      const body = (await response.json()) as ApiErrorBody
      if (isRecord(body.detail)) {
        if (typeof body.detail.code === 'string') {
          code = body.detail.code
        }
        if (typeof body.detail.message === 'string' && body.detail.message.length > 0) {
          detail = body.detail.message
        }
      } else if (typeof body.detail === 'string' && body.detail.length > 0) {
        detail = body.detail
      } else if (Array.isArray(body.detail)) {
        detail = body.detail.map((d) => String(d)).join('; ')
      }
    } catch {
      // non-JSON error body; keep the generic message
    }
    return new ApiError(response.status, detail, code)
  }
}

const DEFAULT_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export const apiClient = new ApiClient({ baseUrl: DEFAULT_BASE_URL, timeoutMs: DEFAULT_TIMEOUT_MS })
