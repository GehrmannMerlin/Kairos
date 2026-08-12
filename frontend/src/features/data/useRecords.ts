/** M-13 数据工作区 records 查询状态（D-040/D-060）。 */

import { ref, watch, type Ref } from 'vue'

import { queryRecords } from './data.api'
import type { RecordPartition, RecordView, RecordListParams } from './types'

export interface UseRecords {
  tab: Ref<RecordPartition | 'all'>
  items: Ref<RecordView[]>
  total: Ref<number>
  partitionCounts: Ref<Record<string, number>>
  loading: Ref<boolean>
  error: Ref<string | null>
  page: Ref<number>
  search: Ref<string>
  params: Ref<RecordListParams>
  load: () => Promise<void>
  setTab: (tab: RecordPartition | 'all') => void
  setSearch: (q: string) => void
  applyParams: (p: RecordListParams) => void
}

export function useRecords(taskId: Ref<string | number>): UseRecords {
  const tab = ref<RecordPartition | 'all'>('all')
  const items = ref<RecordView[]>([])
  const total = ref(0)
  const partitionCounts = ref<Record<string, number>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)
  const page = ref(1)
  const search = ref('')
  const params = ref<RecordListParams>({})
  let seq = 0

  async function load(): Promise<void> {
    const current = ++seq
    loading.value = true
    error.value = null
    try {
      const resp = await queryRecords(taskId.value, {
        ...params.value,
        partition: tab.value === 'all' ? undefined : tab.value,
        q: search.value || undefined,
        page: page.value,
      })
      if (current !== seq) return  // 丢弃过期响应（快速连续搜索）
      items.value = resp.items
      total.value = resp.total
      partitionCounts.value = resp.partition_counts
    } catch (err) {
      if (current !== seq) return
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      if (current === seq) loading.value = false
    }
  }

  function setTab(next: RecordPartition | 'all'): void {
    tab.value = next
    page.value = 1
    void load()
  }

  function setSearch(q: string): void {
    search.value = q
    page.value = 1
    void load()
  }

  function applyParams(p: RecordListParams): void {
    params.value = { ...params.value, ...p }
    page.value = 1
    void load()
  }

  watch(
    taskId,
    () => {
      tab.value = 'all'
      page.value = 1
      void load()
    },
    { immediate: true },
  )

  return {
    tab,
    items,
    total,
    partitionCounts,
    loading,
    error,
    page,
    search,
    params,
    load,
    setTab,
    setSearch,
    applyParams,
  }
}
