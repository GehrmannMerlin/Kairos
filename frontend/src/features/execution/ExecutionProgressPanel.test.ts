import { mount, flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import type { ExecutionView, TimelineEvent } from './types'

const refreshSnapshot = vi.fn(async () => undefined)
const connect = vi.fn()
const disconnect = vi.fn()
const latestEvent = ref<unknown>(null)
const reconcileVersion = ref(0)

const snapshot: ExecutionView = {
  task_id: 25,
  run: {
    run_id: 7,
    state: 'running',
    started_at: '2026-08-16T00:00:00Z',
    finished_at: null,
    plan_version: 1,
    spec_version: 1,
  },
  stages: [],
  urls: {},
  records: {},
  plan: null,
  current_node: {
    node_id: 'n3',
    node_type: 'fetch',
    label: 'fetch',
    state: 'RUNNING',
    attempt: 1,
    safe_message: null,
  },
  last_successful_node: {
    node_id: 'n2',
    node_type: 'link_discovery',
    label: 'link discovery',
    state: 'SUCCEEDED',
    attempt: 1,
    safe_message: null,
  },
  last_activity_at: '2026-08-16T00:01:00Z',
  last_event_id: 17,
  counts: {
    discovered_pages: 6,
    fetched_pages: 4,
    extracted_records: 2,
    validated_records: 1,
  },
  waiting_reason_code: null,
  outcome_code: null,
  legacy_execution_facts: false,
}

const timeline = ref<TimelineEvent[]>([
  {
    event_id: 17,
    timestamp: '2026-08-16T00:01:00Z',
    categories: [],
    stage: 'fetch',
    summary: '页面抓取进行中',
    status: 'RUNNING',
    error_code: null,
    run_id: 7,
    node_run_id: 3,
    node_id: 'n3',
    retry_count: 0,
    tool: null,
    model: null,
    duration_ms: null,
    tokens_in: null,
    tokens_out: null,
    evidence_refs: [],
    trace_ref: null,
  },
])

vi.mock('./useExecution', () => ({
  useExecution: () => ({
    view: ref(snapshot),
    loading: ref(false),
    error: ref(null),
    timeline,
    refreshSnapshot,
  }),
}))

vi.mock('@/features/tasks/useTaskEvents', () => ({
  useTaskEvents: () => ({
    connection: ref('open'),
    latestEvent,
    reconcileVersion,
    connect,
    disconnect,
  }),
}))

import ExecutionProgressPanel from './ExecutionProgressPanel.vue'

describe('ExecutionProgressPanel', () => {
  it('先显示 snapshot，并用 SSE 与重连触发权威刷新', async () => {
    const wrapper = mount(ExecutionProgressPanel, { props: { taskId: '25' } })
    await flushPromises()

    expect(wrapper.text()).toContain('抓取页面')
    expect(wrapper.text()).toContain('已抓取')
    expect(wrapper.text()).toContain('4')
    expect(wrapper.text()).toContain('发现页面链接')
    expect(wrapper.text()).not.toContain('%')
    expect(wrapper.text()).not.toContain('推理')
    expect(connect).toHaveBeenCalledOnce()

    latestEvent.value = { event_id: 18, event_type: 'NODE_COMPLETED' }
    await flushPromises()
    expect(refreshSnapshot).toHaveBeenCalledTimes(1)

    reconcileVersion.value = 1
    await flushPromises()
    expect(refreshSnapshot).toHaveBeenCalledTimes(2)
  })
})
