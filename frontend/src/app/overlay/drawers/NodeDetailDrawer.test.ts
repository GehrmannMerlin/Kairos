import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mount } from '@vue/test-utils'

import type { NodeDetailDto } from '@/features/execution/types'
import NodeDetailDrawer from './NodeDetailDrawer.vue'

const mocks = vi.hoisted(() => ({
  detail: null as NodeDetailDto | null,
}))

vi.mock('@/features/execution/execution.api', () => ({
  getNodeDetail: vi.fn().mockImplementation(async () => mocks.detail),
}))

const DETAIL: NodeDetailDto = {
  node_id: 'n-fetch',
  node_type: 'fetch',
  definition_version: '1.0.0',
  resource_class: 'http',
  depends_on: ['n-source'],
  optional: false,
  fail_policy: 'retry',
  plan_version: 1,
  stage: 'fetch',
  run: { run_id: 1, state: 'COMPLETED', started_at: null, finished_at: null, plan_version: 1, spec_version: 1 },
  parameters_summary: { url_template: 'https://example.com/{id}' },
  execution: {
    event_count: 3,
    last_status: 'SUCCESS',
    last_error: null,
    attempt_count: 2,
    tool: 'http',
    model: 'gpt-4o',
    duration_ms: 320,
    tokens_in: 120,
    tokens_out: 40,
    url_fetched_count: 12,
    record_count: 0,
  },
}

describe('NodeDetailDrawer 节点详情', () => {
  beforeEach(() => {
    mocks.detail = structuredClone(DETAIL)
  })

  it('渲染冻结定义 + 技术统计，只读', async () => {
    const wrapper = mount(NodeDetailDrawer, { props: { payload: { taskId: '9', nodeId: 'n-fetch' } } })
    await flushPromises()
    expect(wrapper.text()).toContain('fetch')
    expect(wrapper.text()).toContain('http')
    expect(wrapper.text()).toContain('320ms')
    expect(wrapper.text()).toContain('2')
    expect(wrapper.text()).toContain('url_template')
  })

  it('无 Retry 按钮（无稳定 Node Retry 命令）', async () => {
    const wrapper = mount(NodeDetailDrawer, { props: { payload: { taskId: '9', nodeId: 'n-fetch' } } })
    await flushPromises()
    const labels = wrapper.findAll('button').map((b) => b.text().trim())
    expect(labels).not.toContain('重试')
  })

  it('不泄漏凭据引用（parameters_summary 已脱敏）', async () => {
    const wrapper = mount(NodeDetailDrawer, { props: { payload: { taskId: '9', nodeId: 'n-fetch' } } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('credential_ref')
  })
})
