import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { mount } from '@vue/test-utils'

import type { DagView, ExecutionView, TimelineEvent } from '@/features/execution/types'
import TaskExecutionView from './TaskExecutionView.vue'

const mocks = vi.hoisted(() => ({
  setFilter: vi.fn(),
  loadMore: vi.fn(),
  toggleDag: vi.fn(),
  openDrawer: vi.fn(),
  view: null as ExecutionView | null,
  timeline: [] as TimelineEvent[],
  filter: '' as string,
  hasMore: false,
  viewMode: 'stage',
  dag: null as DagView | null,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { taskId: '9' } }),
}))
vi.mock('@/app/overlay/drawer.store', () => ({ openDrawer: mocks.openDrawer }))
vi.mock('@/features/execution/useExecution', () => ({
  useExecution: () => ({
    view: ref(mocks.view),
    loading: ref(false),
    error: ref(null),
    timeline: ref(mocks.timeline),
    timelineLoading: ref(false),
    timelineError: ref(null),
    filter: computed(() => mocks.filter),
    hasMore: ref(mocks.hasMore),
    viewMode: computed(() => mocks.viewMode),
    dag: ref(mocks.dag),
    dagLoading: ref(false),
    dagError: ref(null),
    loadMore: mocks.loadMore,
    setFilter: mocks.setFilter,
    toggleDag: mocks.toggleDag,
  }),
}))

const VIEW: ExecutionView = {
  task_id: 9,
  run: {
    run_id: 1,
    state: 'RUNNING',
    started_at: null,
    finished_at: null,
    plan_version: 1,
    spec_version: 1,
  },
  stages: [
    {
      key: 'goal_plan',
      label: '目标与计划',
      state: 'completed',
      event_count: 2,
      url_processed: 0,
      record_count: 0,
      error_count: 0,
    },
    {
      key: 'fetch',
      label: '网页抓取',
      state: 'in_progress',
      event_count: 5,
      url_processed: 12,
      record_count: 0,
      error_count: 1,
    },
  ],
  urls: { discovered: 15, fetched: 12, failed: 1, pending: 2 },
  records: { passed: 3, needs_review: 1 },
  plan: { plan_version: 1, node_count: 3, validation_status: 'VALID' },
  current_node: null,
  last_successful_node: null,
  last_activity_at: null,
  last_event_id: 0,
  counts: {
    discovered_pages: 15,
    fetched_pages: 12,
    extracted_records: 4,
    validated_records: 3,
  },
  waiting_reason_code: null,
  outcome_code: null,
  legacy_execution_facts: true,
}

const TIMELINE: TimelineEvent[] = [
  {
    event_id: 1,
    timestamp: '2026-08-12T00:00:01Z',
    categories: ['error'],
    stage: 'fetch',
    summary: '抓取失败',
    status: 'FAILED',
    error_code: 'network_timeout',
    run_id: 1,
    node_run_id: null,
    node_id: null,
    retry_count: 0,
    tool: 'http',
    model: null,
    duration_ms: null,
    tokens_in: null,
    tokens_out: null,
    evidence_refs: [],
    trace_ref: null,
  },
]

describe('TaskExecutionView 执行详情', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.view = structuredClone(VIEW)
    mocks.timeline = structuredClone(TIMELINE)
    mocks.filter = ''
    mocks.hasMore = false
    mocks.viewMode = 'stage'
    mocks.dag = null
  })

  it('渲染阶段卡与 url/record 事实', () => {
    const wrapper = mount(TaskExecutionView)
    const cards = wrapper.findAll('[data-testid="stage-card"]')
    expect(cards.length).toBe(2)
    expect(wrapper.text()).toContain('目标与计划')
    expect(wrapper.text()).toContain('网页抓取')
    expect(wrapper.text()).toContain('12')
  })

  it('时间线过滤按钮触发 setFilter', async () => {
    const wrapper = mount(TaskExecutionView)
    const errorBtn = wrapper.findAll('.timeline-filter').find((b) => b.text() === '错误')
    expect(errorBtn).toBeDefined()
    await errorBtn!.trigger('click')
    expect(mocks.setFilter).toHaveBeenCalledWith('error')
  })

  it('切换流程图并点击节点打开 Node Detail Drawer', async () => {
    mocks.viewMode = 'dag'
    mocks.dag = {
      task_id: 9,
      plan_version: 1,
      spec_version: 1,
      validation_status: 'VALID',
      stage_status: { fetch: 'in_progress' },
      nodes: [
        {
          node_id: 'n-fetch',
          node_type: 'fetch',
          definition_version: '1.0.0',
          resource_class: 'http',
          depends_on: [],
          optional: false,
          fail_policy: 'retry',
          stage: 'fetch',
          parameters_summary: { url_template: 'https://x' },
          execution: {
            event_count: 0,
            last_status: null,
            last_error: null,
            attempt_count: 0,
            tool: null,
            model: null,
            duration_ms: null,
            tokens_in: null,
            tokens_out: null,
            url_fetched_count: 12,
            record_count: 0,
          },
        },
      ],
      edges: [],
    }
    const wrapper = mount(TaskExecutionView)
    const node = wrapper.find('[data-testid="dag-node"]')
    expect(node.exists()).toBe(true)
    expect(wrapper.text()).toContain('fetch')
    await node.trigger('click')
    expect(mocks.openDrawer).toHaveBeenCalledWith('NODE_DETAIL', { taskId: '9', nodeId: 'n-fetch' })
  })

  it('无任何金额 UI', () => {
    const wrapper = mount(TaskExecutionView)
    const html = wrapper.html()
    expect(html).not.toContain('¥')
    expect(html).not.toContain('$')
    expect(html).not.toContain('费用')
    expect(html).not.toContain('预算')
  })
})
