import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { mount } from '@vue/test-utils'

import TaskQualityView from './TaskQualityView.vue'
import type { QualityView } from '@/features/quality/types'

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  view: null as QualityView | null,
  loading: false,
  error: null as string | null,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { taskId: '9' } }),
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('@/features/quality/useQuality', () => ({
  useQuality: () => ({
    view: ref(mocks.view),
    loading: ref(mocks.loading),
    error: ref(mocks.error),
    reload: vi.fn(),
  }),
}))

const BASE_VIEW: QualityView = {
  task_id: 9,
  dataset_version: 'task-9-v3',
  validation_version: 'v1',
  sampling_policy_version: 'sp1',
  spec_version: 1,
  run_id: 2,
  snapshot_id: 7,
  snapshot_created_at: '2026-08-12T00:00:00Z',
  summary: { total_records: 3, passed: 1, needs_review: 2, rejected: 0 },
  metrics: {
    pass_rate: 0.3333,
    missing_rate: 0.6667,
    duplicate_rate: 0,
    conflict_count: 0,
    source_coverage: 1,
    sampling_accuracy: null,
  },
  field_completeness: [
    { field_name: 'company', total: 3, non_null: 2, missing: 1, completion_rate: 0.6667 },
  ],
  source_coverage: [
    { source_type: 'official_site', eligible: true, covered: true, record_count: 2 },
  ],
  diagnostics: { missing_required: 2, unresolved_conflict: 0, possible_duplicate: 0, low_confidence: 0, rejected: 0 },
  sampling: { sample_count: 1, accuracy: null, sample_refs: [{ record_id: 1 }] },
  items: [
    { key: 'passed', label: '已通过', value: 1, kind: 'count', drilldown: { status: 'passed' } },
    {
      key: 'missing_required',
      label: '字段缺失',
      value: 2,
      kind: 'count',
      drilldown: { status: 'review', review_type: 'missing_required' },
    },
  ],
}

describe('TaskQualityView 质量工作区', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.view = structuredClone(BASE_VIEW)
    mocks.loading = false
    mocks.error = null
  })

  it('渲染后端真实指标卡与版本边界', () => {
    const wrapper = mount(TaskQualityView)
    expect(wrapper.text()).toContain('已通过')
    expect(wrapper.text()).toContain('字段缺失')
    // Metrics Version Boundary：不静默换 Dataset
    expect(wrapper.text()).toContain('task-9-v3')
    // 来源覆盖与抽样来自数据库事实
    expect(wrapper.text()).toContain('official_site')
    expect(wrapper.text()).toContain('1 条抽样')
  })

  it('点击指标卡跳转 M-13 Data Deep Link', async () => {
    const wrapper = mount(TaskQualityView)
    const card = wrapper.findAll('[data-testid="quality-card"]').find((c) =>
      c.text().includes('字段缺失'),
    )
    expect(card).toBeDefined()
    await card!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({
      name: 'task-data',
      params: { taskId: '9' },
      query: { status: 'review', review_type: 'missing_required' },
    })
  })

  it('空任务显示明确 empty state', () => {
    mocks.view = {
      ...BASE_VIEW,
      summary: { total_records: 0, passed: 0, needs_review: 0, rejected: 0 },
      field_completeness: [],
      source_coverage: [],
      sampling: { sample_count: 0, accuracy: null, sample_refs: [] },
      items: [],
    }
    const wrapper = mount(TaskQualityView)
    expect(wrapper.text()).toContain('暂无')
  })

  it('无任何编辑/审核动作（Quality 只诊断）', () => {
    const wrapper = mount(TaskQualityView)
    const labels = wrapper.findAll('button').map((b) => b.text().trim())
    expect(labels).not.toContain('通过')
    expect(labels).not.toContain('拒绝')
    expect(labels).not.toContain('修正')
    expect(labels).not.toContain('让 Agent 重新处理')
  })
})
