/** M-14 Execution 工作区状态（D-055/D-063）：阶段 + 时间线 + 只读 DAG。 */

import { ref, watch, type Ref } from 'vue'

import { getDag, getExecution, getTimeline } from './execution.api'
import type { DagView, ExecutionView, TimelineCategory, TimelineEvent } from './types'

const TIMELINE_PAGE_SIZE = 50

export interface UseExecution {
  view: Ref<ExecutionView | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  timeline: Ref<TimelineEvent[]>
  timelineLoading: Ref<boolean>
  timelineError: Ref<string | null>
  filter: Ref<TimelineCategory | ''>
  hasMore: Ref<boolean>
  viewMode: Ref<'stage' | 'dag'>
  dag: Ref<DagView | null>
  dagLoading: Ref<boolean>
  dagError: Ref<string | null>
  loadMore: () => Promise<void>
  setFilter: (category: TimelineCategory | '') => void
  toggleDag: () => void
  refreshSnapshot: () => Promise<void>
  mergeTimelineEvent: (event: TimelineEvent) => void
}

export function useExecution(taskId: Ref<string | number>): UseExecution {
  const view = ref<ExecutionView | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const timeline = ref<TimelineEvent[]>([])
  const timelineLoading = ref(false)
  const timelineError = ref<string | null>(null)
  const filter = ref<TimelineCategory | ''>('')
  const nextCursor = ref<number | null>(null)
  const hasMore = ref(false)
  const viewMode = ref<'stage' | 'dag'>('stage')
  const dag = ref<DagView | null>(null)
  const dagLoading = ref(false)
  const dagError = ref<string | null>(null)
  let seq = 0
  let timelineSeq = 0

  async function loadOverview(): Promise<void> {
    const current = ++seq
    loading.value = true
    error.value = null
    try {
      const data = await getExecution(taskId.value)
      if (current !== seq) return
      view.value = data
    } catch (err) {
      if (current !== seq) return
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      if (current === seq) loading.value = false
    }
  }

  async function loadTimeline(afterId: number | null): Promise<void> {
    const current = ++timelineSeq
    timelineLoading.value = true
    timelineError.value = null
    try {
      const data = await getTimeline(taskId.value, {
        category: filter.value || undefined,
        afterId: afterId ?? undefined,
        limit: TIMELINE_PAGE_SIZE,
      })
      if (current !== timelineSeq) return
      timeline.value = afterId ? [...timeline.value, ...data.items] : data.items
      nextCursor.value = data.next_cursor
      hasMore.value = data.has_more
    } catch (err) {
      if (current !== timelineSeq) return
      timelineError.value = err instanceof Error ? err.message : String(err)
    } finally {
      if (current === timelineSeq) timelineLoading.value = false
    }
  }

  async function loadDag(): Promise<void> {
    dagLoading.value = true
    dagError.value = null
    try {
      dag.value = await getDag(taskId.value)
    } catch (err) {
      dagError.value = err instanceof Error ? err.message : String(err)
    } finally {
      dagLoading.value = false
    }
  }

  function loadMore(): Promise<void> {
    return loadTimeline(nextCursor.value)
  }

  function setFilter(category: TimelineCategory | ''): void {
    filter.value = category
    nextCursor.value = null
    timeline.value = []
    void loadTimeline(null)
  }

  function toggleDag(): void {
    viewMode.value = viewMode.value === 'stage' ? 'dag' : 'stage'
    if (viewMode.value === 'dag' && !dag.value && !dagLoading.value) {
      void loadDag()
    }
  }

  async function refreshSnapshot(): Promise<void> {
    await Promise.all([loadOverview(), loadTimeline(null)])
  }

  function mergeTimelineEvent(event: TimelineEvent): void {
    if (timeline.value.some((item) => item.event_id === event.event_id)) return
    timeline.value = [...timeline.value, event].sort((a, b) => a.event_id - b.event_id)
  }

  watch(
    taskId,
    () => {
      nextCursor.value = null
      timeline.value = []
      dag.value = null
      void loadOverview()
      void loadTimeline(null)
    },
    { immediate: true },
  )

  return {
    view,
    loading,
    error,
    timeline,
    timelineLoading,
    timelineError,
    filter,
    hasMore,
    viewMode,
    dag,
    dagLoading,
    dagError,
    loadMore,
    setFilter,
    toggleDag,
    refreshSnapshot,
    mergeTimelineEvent,
  }
}
