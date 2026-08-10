import { ApiError } from '@/app/error/ApiError'

export interface ApiClientOptions {
  /** Base path of the API, e.g. `/api` (same-origin behind a reverse proxy). */
  baseUrl: string
  timeoutMs?: number
}

interface ApiErrorBody {
  detail?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

/**
 * Thin JSON fetch wrapper. Later modules add auth headers, SSE, and command/query
 * DTOs on top of this single entry point.
 */
export class ApiClient {
  private readonly baseUrl: string
  private readonly timeoutMs: number

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, '')
    this.timeoutMs = options.timeoutMs ?? 10_000
  }

  async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>('GET', path, undefined, signal)
  }

  async post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>('POST', path, body, signal)
  }

  async patch<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>('PATCH', path, body, signal)
  }

  async delete<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>('DELETE', path, undefined, signal)
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    externalSignal?: AbortSignal,
  ): Promise<T> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
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
      throw new ApiError(0, '网络请求失败或超时', error)
    } finally {
      clearTimeout(timer)
      externalSignal?.removeEventListener('abort', abortFromOutside)
    }
  }

  private async toApiError(response: Response): Promise<ApiError> {
    let detail: string = `请求失败 (${response.status})`
    try {
      const body = (await response.json()) as ApiErrorBody
      if (typeof body.detail === 'string' && body.detail.length > 0) {
        detail = body.detail
      } else if (Array.isArray(body.detail)) {
        detail = body.detail.map((d) => String(d)).join('; ')
      } else if (isRecord(body.detail) && typeof body.detail.message === 'string') {
        detail = body.detail.message
      }
    } catch {
      // non-JSON error body; keep the generic message
    }
    return new ApiError(response.status, detail)
  }
}

const DEFAULT_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export const apiClient = new ApiClient({ baseUrl: DEFAULT_BASE_URL, timeoutMs: 10_000 })
