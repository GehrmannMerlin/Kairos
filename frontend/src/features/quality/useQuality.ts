/** M-14 Quality 页面查询状态（D-062）。明确 idle/loading/success/empty/error。 */

import { ref, watch, type Ref } from 'vue'

import { getQuality } from './quality.api'
import type { QualityView } from './types'

export interface UseQuality {
  view: Ref<QualityView | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  reload: () => Promise<void>
}

export function useQuality(taskId: Ref<string | number>): UseQuality {
  const view = ref<QualityView | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let seq = 0

  async function reload(): Promise<void> {
    const current = ++seq
    loading.value = true
    error.value = null
    try {
      const data = await getQuality(taskId.value)
      if (current !== seq) return
      view.value = data
    } catch (err) {
      if (current !== seq) return
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      if (current === seq) loading.value = false
    }
  }

  watch(
    taskId,
    () => {
      void reload()
    },
    { immediate: true },
  )

  return { view, loading, error, reload }
}
