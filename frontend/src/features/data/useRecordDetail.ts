/** M-13 Record Drawer 数据 + 审核动作（D-041/D-042）。 */

import { ref, type Ref } from 'vue'

import { getRecordDetail, reviewRecord } from './data.api'
import type { FieldEdit, RecordDetailView } from './types'

export interface UseRecordDetail {
  detail: Ref<RecordDetailView | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  can: (action: string) => boolean
  approve: (reason?: string) => Promise<void>
  reject: (reason?: string) => Promise<void>
  edit: (edits: FieldEdit[]) => Promise<void>
  reprocess: (reason?: string) => Promise<void>
}

export function useRecordDetail(taskId: string | number, recordId: number): UseRecordDetail {
  const detail = ref<RecordDetailView | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      detail.value = await getRecordDetail(taskId, recordId)
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      loading.value = false
    }
  }
  void load()

  const can = (action: string): boolean => !!detail.value?.allowed_actions.includes(action)

  async function run(
    action: 'approve' | 'reject' | 'edit' | 'agent_reevaluate',
    reason: string | undefined,
    edits?: FieldEdit[],
  ): Promise<void> {
    if (!detail.value) return
    await reviewRecord(taskId, recordId, {
      action,
      reason,
      edits,
      expected_data_version: detail.value.data_version,
    })
    await load()
  }

  return {
    detail,
    loading,
    error,
    can,
    approve: (reason) => run('approve', reason),
    reject: (reason) => run('reject', reason),
    edit: (edits) => run('edit', undefined, edits),
    reprocess: (reason) => run('agent_reevaluate', reason),
  }
}
