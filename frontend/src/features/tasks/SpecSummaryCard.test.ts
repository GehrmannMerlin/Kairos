import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SpecSummaryCard from '@/features/tasks/SpecSummaryCard.vue'
import type { SpecDraftPayload } from '@/features/tasks/spec.types'

const draft: SpecDraftPayload = {
  schema_version: 'm06.1',
  task_type: 'EXPLORATORY',
  task_name: null,
  goal: '搜集深圳的工业自动化设备供应商',
  fields: [
    { name: '公司名', type: 'text', required: true },
    { name: '官网', type: 'url', required: false },
  ],
  auto_expand_fields: true,
  source_scope: { mode: 'EXPLORATORY', seed_urls: [], source_hints: [] },
  completion_conditions: [{ kind: 'min_records', target: 20 }],
  advanced_settings: {},
  field_expansion: {},
}

describe('SpecSummaryCard 摘要卡', () => {
  it('渲染真实 Draft 字段与任务类型，未确认时显示草稿', () => {
    const wrapper = mount(SpecSummaryCard, { props: { payload: draft } })

    expect(wrapper.text()).toContain('探索式搜集')
    expect(wrapper.text()).toContain('公司名')
    expect(wrapper.text()).toContain('必填')
    expect(wrapper.text()).toContain('min_records')
    expect(wrapper.text()).toContain('草稿')
    expect(wrapper.text()).toContain('确认并执行')
  })

  it('已确认版本显示 vN 并隐藏确认按钮', () => {
    const wrapper = mount(SpecSummaryCard, {
      props: { payload: draft, confirmedVersion: 1 },
    })

    expect(wrapper.text()).toContain('已确认 v1')
    expect(wrapper.text()).not.toContain('确认并执行')
  })

  it('确认按钮发出 confirm 事件', async () => {
    const wrapper = mount(SpecSummaryCard, { props: { payload: draft } })
    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('确认并执行'))
    expect(confirmBtn).toBeTruthy()
    await confirmBtn!.trigger('click')
    expect(wrapper.emitted('confirm')).toBeTruthy()
  })

  it('部分数据不崩溃（防御性渲染）', () => {
    const wrapper = mount(SpecSummaryCard, {
      props: { payload: { goal: '仅有目标' } as unknown as SpecDraftPayload },
    })
    expect(wrapper.text()).toContain('仅有目标')
  })
})
