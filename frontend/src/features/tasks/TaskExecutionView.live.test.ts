import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, nextTick, ref, type Ref } from 'vue'

import type { DagView, ExecutionView, TimelineEvent } from '@/features/execution/types'
import TaskExecutionView from './TaskExecutionView.vue'

const mocks = vi.hoisted(() => ({
  connectLive: vi.fn(),
  disconnectLive: vi.fn(),
  refreshSnapshot: vi.fn(() => Promise.resolve()),
  setFilter: vi.fn(),
  loadMore: vi.fn(),
  toggleDag: vi.fn(),
  openDrawer: vi.fn(),
  filter: '' as string,
  hasMore: false,
  viewMode: 'stage' as 'stage' | 'dag',
  dag: null as DagView | null,
  viewRef: null as unknown as Ref<ExecutionView | null>,
  timelineRef: null as unknown as Ref<TimelineEvent[]>,
  liveRef: null as unknown as Ref<'idle' | 'connecting' | 'open' | 'reconnecting'>,
  reconcileVersionRef: null as unknown as Ref<number>,
}))

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { taskId: '25' } }) }))
vi.mock('@/app/overlay/drawer.store', () => ({ openDrawer: mocks.openDrawer }))
vi.mock('@/features/execution/useExecution', () => ({
  useExecution: () => ({
    view: mocks.viewRef,
    loading: ref(false),
    error: ref(null),
    timeline: mocks.timelineRef,
    timelineLoading: ref(false),
    timelineError: ref(null),
    filter: computed(() => mocks.filter),
    hasMore: ref(false),
    viewMode: computed(() => mocks.viewMode),
    dag: ref(null),
    dagLoading: ref(false),
    dagError: ref(null),
    live: mocks.liveRef,
    reconcileVersion: mocks.reconcileVersionRef,
    loadMore: mocks.loadMore,
    setFilter: mocks.setFilter,
    toggleDag: mocks.toggleDag,
    refreshSnapshot: mocks.refreshSnapshot,
    connectLive: mocks.connectLive,
    disconnectLive: mocks.disconnectLive,
  }),
}))

const VIEW: ExecutionView = {
  task_id: 25,
  run: {
    run_id: 3,
    state: 'RUNNING',
    started_at: null,
    finished_at: null,
    plan_version: 2,
    spec_version: 1,
  },
  stages: [],
  urls: { discovered: 0, fetched: 0, failed: 0, pending: 0 },
  records: { passed: 0, needs_review: 0 },
  plan: { plan_version: 2, node_count: 1, validation_status: 'VALID' },
  current_node: {
    node_id: 'n3',
    node_type: 'fetch',
    label: '抓取',
    state: 'RUNNING',
    attempt: 1,
    safe_message: null,
  },
  last_successful_node: null,
  last_activity_at: null,
  last_event_id: 0,
  counts: {
    discovered_pages: 0,
    fetched_pages: 0,
    extracted_records: 0,
    validated_records: 0,
  },
  waiting_reason_code: null,
  outcome_code: null,
  legacy_execution_facts: true,
}

function buildEvent(partial: Record<string, unknown>): TimelineEvent {
  return {
    event_id: 1,
    timestamp: '2026-08-21T12:00:00Z',
    categories: [],
    stage: '',
    summary: '',
    status: null,
    error_code: null,
    run_id: 1,
    node_run_id: null,
    node_id: null,
    retry_count: 0,
    tool: null,
    model: null,
    duration_ms: null,
    tokens_in: null,
    tokens_out: null,
    evidence_refs: [],
    trace_ref: null,
    ...partial,
  } as TimelineEvent
}

function emitTimeline(partial: Record<string, unknown>): void {
  mocks.timelineRef.value = [...mocks.timelineRef.value, buildEvent(partial)]
}

describe('TaskExecutionView 实时时间线', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.viewRef = ref(structuredClone(VIEW))
    mocks.timelineRef = ref<TimelineEvent[]>([])
    mocks.liveRef = ref<'idle' | 'connecting' | 'open' | 'reconnecting'>('idle')
    mocks.reconcileVersionRef = ref(0)
    mocks.filter = ''
    mocks.hasMore = false
    mocks.viewMode = 'stage'
    mocks.dag = null
  })

  it('run 激活时自动连接流并显示实时状态', async () => {
    const wrapper = mount(TaskExecutionView, { props: { taskId: '25' } })
    await flushPromises()
    expect(mocks.connectLive).toHaveBeenCalled()
    expect(wrapper.text()).toContain('实时')
  })

  it('新事件追加为步骤行且当前节点高亮', async () => {
    const wrapper = mount(TaskExecutionView, { props: { taskId: '25' } })
    await flushPromises()
    emitTimeline({
      event_id: 12,
      summary: '抓取完成',
      status: 'COMPLETED',
      node_id: 'n3',
      stage: 'fetch',
      node_type: 'fetch',
    })
    await nextTick()
    expect(wrapper.text()).toContain('抓取完成')
    expect(wrapper.find('.timeline-step-row--active').exists()).toBe(true)
  })

  it('进行中状态显示脉冲指示', async () => {
    const wrapper = mount(TaskExecutionView, { props: { taskId: '25' } })
    await flushPromises()
    emitTimeline({
      event_id: 13,
      status: 'RUNNING',
      node_id: 'n4',
      summary: '提取字段',
      stage: 'extraction',
    })
    await nextTick()
    expect(wrapper.find('.step-status--running').exists()).toBe(true)
  })

  it('流 reconcile 版本递增时刷新快照', async () => {
    mount(TaskExecutionView, { props: { taskId: '25' } })
    await flushPromises()
    mocks.reconcileVersionRef.value = 1
    await nextTick()
    expect(mocks.refreshSnapshot).toHaveBeenCalled()
  })
})
