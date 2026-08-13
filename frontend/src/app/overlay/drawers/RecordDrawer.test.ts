import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

import RecordDrawer from './RecordDrawer.vue'

const mocks = vi.hoisted(() => ({
  openDrawer: vi.fn(),
  useRecordDetail: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  edit: vi.fn(),
  reprocess: vi.fn(),
}))

vi.mock('@/app/overlay/drawer.store', () => ({ openDrawer: mocks.openDrawer }))
vi.mock('@/features/data/useRecordDetail', () => ({ useRecordDetail: mocks.useRecordDetail }))

const CORE_ACTIONS = ['approve', 'reject', 'edit', 'agent_reevaluate']

function fixture(detailOverrides: Record<string, unknown> = {}) {
  const detail = {
    record_id: 42,
    partition: 'needs_review',
    review_type: 'missing_required',
    review_reason: 'missing_required',
    data_version: 1,
    allowed_actions: CORE_ACTIONS,
    fields: [
      {
        field_name: '标题',
        value: '旧值',
        original_value: null,
        value_source: 'EXTRACTED',
        extract_method: 'llm',
        extractor_version: 'm11.1',
        confidence: 0.7,
        source_url: 'https://example.com',
        snapshot_id: 7,
      },
    ],
    created_at: '2026-08-12T00:00:00Z',
    updated_at: '2026-08-12T00:00:00Z',
    ...detailOverrides,
  }
  const allowed = detail.allowed_actions as string[]
  return {
    detail: ref(detail),
    loading: ref(false),
    error: ref(null),
    can: (a: string) => allowed.includes(a),
    approve: mocks.approve,
    reject: mocks.reject,
    edit: mocks.edit,
    reprocess: mocks.reprocess,
  }
}

describe('RecordDrawer 记录详情', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useRecordDetail.mockImplementation(() => fixture())
  })

  it('展示字段值 + 证据元数据，并按 allowed_actions 渲染审核按钮', () => {
    const wrapper = mount(RecordDrawer, { props: { payload: { taskId: '9', recordId: 42 } } })
    expect(wrapper.text()).toContain('旧值')
    expect(wrapper.text()).toContain('m11.1')
    expect(wrapper.text()).toContain('通过')
    expect(wrapper.text()).toContain('拒绝')
    expect(wrapper.text()).toContain('让 Agent 重新处理')
    expect(wrapper.text()).toContain('查看网页证据')
  })

  it('USER_OVERRIDE 字段标记人工修正并保留原值', () => {
    mocks.useRecordDetail.mockReturnValueOnce(
      fixture({
        review_type: null,
        review_reason: null,
        data_version: 2,
        allowed_actions: ['edit'],
        fields: [
          {
            field_name: '标题',
            value: '新值',
            original_value: '旧值',
            value_source: 'USER_OVERRIDE',
            extract_method: null,
            extractor_version: null,
            confidence: null,
            source_url: null,
            snapshot_id: null,
          },
        ],
      }),
    )
    const wrapper = mount(RecordDrawer, { props: { payload: { taskId: '9', recordId: 1 } } })
    expect(wrapper.text()).toContain('人工修正')
    expect(wrapper.text()).toContain('旧值')
    expect(wrapper.text()).not.toContain('通过')
  })
})
