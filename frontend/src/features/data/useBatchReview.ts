/** M-13 批量审核（D-061）：调 batch-review API，报告单条失败。 */

import { ref, type Ref } from 'vue'

import { batchReview } from './data.api'

export type BatchAction = 'approve' | 'reject' | 'agent_reevaluate'

export interface UseBatchReview {
  pending: Ref<boolean>
  error: Ref<string | null>
  run: (action: BatchAction, reason?: string) => Promise<boolean>
}

export function useBatchReview(
  taskId: string | number,
  recordIds: Ref<number[]>,
  dataVersions: Ref<Record<number, number>>,
  onDone: () => void,
): UseBatchReview {
  const pending = ref(false)
  const error = ref<string | null>(null)

  async function run(action: BatchAction, reason?: string): Promise<boolean> {
    if (recordIds.value.length === 0) return false
    pending.value = true
    error.value = null
    try {
      const resp = await batchReview(taskId, {
        action,
        record_ids: recordIds.value,
        reason,
        expected_data_versions: dataVersions.value,
      })
      const failed = resp.results.filter((r) => !r.ok)
      if (failed.length > 0) {
        error.value = `部分失败：${failed.map((r) => `${r.record_id}:${r.error}`).join('；')}`
      }
      onDone()
      return failed.length === 0
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      return false
    } finally {
      pending.value = false
    }
  }

  return { pending, error, run }
}
