import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
}))

vi.mock('@/features/templates/templates.api', () => ({
  useTemplate: vi.fn().mockResolvedValue({ task_id: 9 }),
}))

import * as templatesApi from '@/features/templates/templates.api'
import TemplateVariablesSheet from '@/app/overlay/modals/TemplateVariablesSheet.vue'

const template = {
  template_id: 'tpl-1',
  version: 1,
  name: '供应商模板',
  task_type: 'EXPLORATORY',
  goal_template: '帮我搜集{city}的供应商',
  variables: [
    { name: 'city', label: '城市', required: true },
    { name: 'note', label: '备注', required: false },
  ],
  field_schema: [],
  completion_conditions: [],
  advanced_settings: {},
  field_expansion: {},
  is_favorite: false,
  created_at: '2026-08-10T00:00:00Z',
}

function mountSheet() {
  return mount(TemplateVariablesSheet, { props: { payload: { template } } })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TemplateVariablesSheet 模板变量', () => {
  it('必填变量缺失时提示且不调用 useTemplate', async () => {
    const wrapper = mountSheet()
    const buttons = wrapper.findAll('button')
    await buttons.find((b) => b.text().includes('使用模板创建任务'))!.trigger('click')
    await flushPromises()

    expect(templatesApi.useTemplate).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('城市')
  })

  it('填写变量 → useTemplate → 进入 /tasks/:id/chat', async () => {
    const wrapper = mountSheet()
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('深圳')
    const buttons = wrapper.findAll('button')
    await buttons.find((b) => b.text().includes('使用模板创建任务'))!.trigger('click')
    await flushPromises()

    expect(templatesApi.useTemplate).toHaveBeenCalledWith('tpl-1', { city: '深圳' })
    expect(pushMock).toHaveBeenCalledWith('/tasks/9/chat')
  })
})
