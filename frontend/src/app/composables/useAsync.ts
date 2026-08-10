import { ref, type Ref } from 'vue'

export type AsyncStatus = 'idle' | 'loading' | 'success' | 'error'

export interface UseAsyncState<T> {
  status: Ref<AsyncStatus>
  data: Ref<T | null>
  error: Ref<string | null>
  run: () => Promise<void>
  reset: () => void
}

/** Minimal async-state helper satisfying the idle/loading/success/error contract. */
export function useAsync<T>(task: () => Promise<T>): UseAsyncState<T> {
  const status = ref<AsyncStatus>('idle') as Ref<AsyncStatus>
  const data = ref<T | null>(null) as Ref<T | null>
  const error = ref<string | null>(null) as Ref<string | null>

  async function run(): Promise<void> {
    status.value = 'loading'
    error.value = null
    try {
      data.value = await task()
      status.value = 'success'
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      data.value = null
      status.value = 'error'
    }
  }

  function reset(): void {
    status.value = 'idle'
    data.value = null
    error.value = null
  }

  return { status, data, error, run, reset }
}
