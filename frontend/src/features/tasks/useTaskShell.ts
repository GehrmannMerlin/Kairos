import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

import { ApiError } from '@/app/error/ApiError'
import { mapApiError } from '@/app/error/apiErrorMapper'
import { getTask, type TaskShellDto } from '@/features/tasks/tasks.api'

export interface UseTaskShell {
  summary: Ref<TaskShellDto | null>
  loading: Ref<boolean>
  /** owner-safe 404：资源不存在或无权访问，前端不泄漏任何 task 元数据。 */
  notFound: Ref<boolean>
  error: Ref<string | null>
  state: ComputedRef<string | null>
  allowedActions: ComputedRef<string[]>
  /** allowed_actions 统一消费；按钮显隐/禁用只来自后端。 */
  can: (action: string) => boolean
}

/** 每个 Task 路由实例独立持有 shell 状态；taskId 变化时重新加载。 */
export function useTaskShell(taskId: Ref<string>): UseTaskShell {
  const summary = ref<TaskShellDto | null>(null)
  const loading = ref(false)
  const notFound = ref(false)
  const error = ref<string | null>(null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    notFound.value = false
    summary.value = null
    try {
      summary.value = await getTask(taskId.value)
    } catch (err) {
      if (err instanceof ApiError && mapApiError(err).kind === 'not_found') {
        notFound.value = true
      } else {
        error.value = err instanceof Error ? err.message : String(err)
      }
    } finally {
      loading.value = false
    }
  }

  watch(taskId, () => void load(), { immediate: true })

  const state = computed<string | null>(() => summary.value?.state ?? null)
  const allowedActions = computed<string[]>(() => summary.value?.allowed_actions ?? [])

  function can(action: string): boolean {
    return allowedActions.value.includes(action)
  }

  return { summary, loading, notFound, error, state, allowedActions, can }
}
