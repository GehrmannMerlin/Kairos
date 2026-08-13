import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import TaskDataView from './TaskDataView.vue'

const mocks = vi.hoisted(() => ({
  setTab: vi.fn(),
  load: vi.fn(),
  openDrawer: vi.fn(),
  useRecordEvents: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { taskId: '9' }, query: { status: 'review' } }),
}))
vi.mock('@/app/overlay/drawer.store', () => ({ openDrawer: mocks.openDrawer }))
vi.mock('@/features/data/useRecords', () => ({
  useRecords: () => ({
    tab: { value: 'all' },
    items: { value: [] },
    total: { value: 3 },
    partitionCounts: { value: { passed: 1, needs_review: 2 } },
    loading: { value: false },
    error: { value: null },
    page: { value: 1 },
    search: { value: '' },
    params: { value: {} },
    load: mocks.load,
    setTab: mocks.setTab,
    setSearch: vi.fn(),
    applyParams: vi.fn(),
  }),
}))
vi.mock('@/features/data/useRecordEvents', () => ({ useRecordEvents: mocks.useRecordEvents }))

describe('TaskDataView 数据工作区', () => {
  beforeEach(() => vi.clearAllMocks())

  it('渲染三分区 Tab 与后端真实计数', () => {
    const wrapper = mount(TaskDataView)
    const tabs = wrapper.findAll('.data-tab')
    expect(tabs.length).toBe(4)
    expect(wrapper.text()).toContain('全部')
    expect(wrapper.text()).toContain('已通过')
    expect(wrapper.text()).toContain('待复核')
    expect(wrapper.text()).toContain('已拒绝')
    // 计数来自 partition_counts（后端事实）
    expect(wrapper.text()).toContain('3')
    expect(wrapper.text()).toContain('2')
  })

  it('Deep Link status=review 落到待复核 Tab', () => {
    mount(TaskDataView)
    expect(mocks.setTab).toHaveBeenCalledWith('needs_review')
  })

  it('订阅 record.* SSE 事件触发刷新', () => {
    mount(TaskDataView)
    expect(mocks.useRecordEvents).toHaveBeenCalled()
  })
})
