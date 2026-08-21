/** M-14 Execution 工作区状态（D-055/D-063）：阶段 + 时间线 + 只读 DAG。 */

import { ref, watch, type Ref } from 'vue'

import {
  getDag,
  getExecution,
  getTimeline,
  openExecutionTimelineStream,
  parseTimelineSseMessage,
} from './execution.api'
import type { DagView, ExecutionView, TimelineCategory, TimelineEvent } from './types'

const TIMELINE_PAGE_SIZE = 50
const LIVE_REFRESH_DEBOUNCE_MS = 500

export type LiveState = 'idle' | 'connecting' | 'open' | 'reconnecting'

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
  live: Ref<LiveState>
  reconcileVersion: Ref<number>
  loadMore: () => Promise<void>
  setFilter: (category: TimelineCategory | '') => void
  toggleDag: () => void
  refreshSnapshot: () => Promise<void>
  refreshLiveOverview: () => Promise<void>
  mergeTimelineEvent: (event: TimelineEvent) => void
  connectLive: () => void
  disconnectLive: () => void
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
  const live = ref<LiveState>('idle')
  const reconcileVersion = ref(0)
  let seq = 0
  let timelineSeq = 0
  let streamSource: EventSource | null = null
  let liveTimer: number | undefined
  let lastStreamEventId = 0
  let refreshToken = 0

  // 经 self 暴露的方法引用外部返回对象，使 live 内部刷新路径可被外部 spy 观测。
  const self: UseExecution = {
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
    live,
    reconcileVersion,
    loadMore,
    setFilter,
    toggleDag,
    refreshSnapshot,
    refreshLiveOverview,
    mergeTimelineEvent,
    connectLive,
    disconnectLive,
  }

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

  function loadDagIfNeeded(): Promise<void> {
    if (dag.value || dagLoading.value) return Promise.resolve()
    return loadDag()
  }

  /**
   * 轻量刷新：只刷新 overview（必要时 DAG），不重置 timeline。
   * 供流式事件节流刷新使用——snapshot 权威、stream 增量，绝不在刷新时丢弃已完成节点。
   */
  async function refreshLiveOverview(): Promise<void> {
    await Promise.all([loadOverview(), loadDagIfNeeded()])
  }

  function mergeTimelineEvent(event: TimelineEvent): void {
    if (timeline.value.some((item) => item.event_id === event.event_id)) return
    timeline.value = [...timeline.value, event].sort((a, b) => a.event_id - b.event_id)
  }

  /**
   * 事件 burst 节流刷新：500ms 窗口内多条事件合并为一次轻量刷新。
   * 经 self.refreshLiveOverview 调用，便于外部 spy 可观测，且保持 timeline 增量以流为准。
   */
  function scheduleCoalescedRefresh(): void {
    clearTimeout(liveTimer)
    liveTimer = window.setTimeout(() => {
      const token = ++refreshToken
      void self.refreshLiveOverview().finally(() => {
        if (token !== refreshToken) return
      })
    }, LIVE_REFRESH_DEBOUNCE_MS)
  }

  /** 打开 timeline SSE 流（owner-safe、task 维度；run 为空时仅无事件，仍建流）。 */
  function connectLive(): void {
    disconnectLive()
    live.value = 'connecting'
    streamSource = openExecutionTimelineStream(taskId.value, {
      lastEventId: lastStreamEventId || undefined,
    })
    streamSource.addEventListener('timeline', (e: MessageEvent) => {
      const dto = parseTimelineSseMessage(String(e.data))
      if (!dto || dto.event_id <= lastStreamEventId) return
      lastStreamEventId = dto.event_id
      mergeTimelineEvent(dto)
      scheduleCoalescedRefresh()
    })
    streamSource.onopen = () => {
      if (live.value === 'reconnecting') {
        reconcileVersion.value += 1
        void self.refreshSnapshot()
      }
      live.value = 'open'
    }
    streamSource.onerror = () => {
      live.value = 'reconnecting'
    }
  }

  function disconnectLive(): void {
    if (streamSource) {
      streamSource.close()
      streamSource = null
    }
    clearTimeout(liveTimer)
    live.value = 'idle'
  }

  watch(
    taskId,
    () => {
      disconnectLive()
      lastStreamEventId = 0
      nextCursor.value = null
      timeline.value = []
      dag.value = null
      void loadOverview()
      void loadTimeline(null)
    },
    { immediate: true },
  )

  return self
}
