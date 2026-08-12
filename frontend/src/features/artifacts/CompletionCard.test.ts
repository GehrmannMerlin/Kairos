import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import type { CompletionCardView } from '@/features/artifacts/types'
import CompletionCard from './CompletionCard.vue'

const mocks = vi.hoisted(() => ({ openModal: vi.fn() }))
vi.mock('@/app/overlay/modal.store', () => ({ openModal: mocks.openModal }))

const RouterLinkStub = { template: '<a><slot /></a>' }

const NORMAL: CompletionCardView = {
  task_id: 9,
  completion_id: 1,
  status: 'NORMAL_COMPLETED',
  reason: '指定来源范围已全部处理',
  completion_type: 'directional_scope_complete',
  is_partial: false,
  qualified_record_count: 3,
  partition_counts: { passed: 3, needs_review: 0, rejected: 0 },
  url_processed: 5,
  runtime_limit_reason: null,
  scope_completion_metadata: {},
  can_view_data: true,
  can_view_quality: true,
  can_export_formal: true,
  can_export_review: false,
}

const PARTIAL: CompletionCardView = {
  task_id: 9,
  completion_id: 2,
  status: 'PARTIALLY_COMPLETED',
  reason: '未达到最低合格记录或尚未饱和',
  completion_type: 'runtime_limit',
  is_partial: true,
  qualified_record_count: 1,
  partition_counts: { passed: 1, needs_review: 2, rejected: 1 },
  url_processed: 2,
  runtime_limit_reason: '达到最长运行时间',
  scope_completion_metadata: { eligible_urls: 5, terminal_urls: 2 },
  can_view_data: true,
  can_view_quality: true,
  can_export_formal: true,
  can_export_review: false,
}

describe('CompletionCard', () => {
  beforeEach(() => mocks.openModal.mockClear())

  it('NORMAL 渲染计数与导出动作', async () => {
    const wrapper = mount(CompletionCard, {
      props: { card: NORMAL, taskId: '9' },
      global: { stubs: { RouterLink: RouterLinkStub } },
    })
    expect(wrapper.find('[data-testid="completion-card"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('任务已完成')
    expect(wrapper.text()).toContain('已通过')
    expect(wrapper.findAll('.completion-card__stat dd')[0].text()).toBe('3')
    await wrapper.find('[data-testid="completion-export"]').trigger('click')
    expect(mocks.openModal).toHaveBeenCalledWith('EXPORT', { taskId: '9', filter: {} })
  })

  it('PARTIAL 渲染停止原因、未覆盖摘要，无百分比', () => {
    const wrapper = mount(CompletionCard, {
      props: { card: PARTIAL, taskId: '9' },
      global: { stubs: { RouterLink: RouterLinkStub } },
    })
    expect(wrapper.text()).toContain('部分完成')
    expect(wrapper.text()).toContain('停止原因：达到最长运行时间')
    expect(wrapper.text()).toContain('未覆盖范围：已处理 2 / 5 个来源页面')
    expect(wrapper.text()).not.toMatch(/\d+(\.\d+)?\s*%/)
  })
})
