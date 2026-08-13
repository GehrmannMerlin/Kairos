import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/features/tasks/chat.api', () => ({
  updateSpecDraft: vi.fn().mockResolvedValue({ task_id: 1, payload: {} }),
  confirmSpec: vi.fn().mockResolvedValue({ task_id: 1, spec_version: 1, state: 'QUEUED' }),
}))

import * as chatApi from '@/features/tasks/chat.api'
import CollectionSpecEditorSheet from '@/app/overlay/modals/CollectionSpecEditorSheet.vue'
import type { SpecDraftPayload } from '@/features/tasks/spec.types'

const basePayload: SpecDraftPayload = {
  schema_version: 'm06.1',
  task_type: 'EXPLORATORY',
  task_name: null,
  goal: '搜集深圳供应商',
  fields: [{ name: '公司名', type: 'text', required: true }],
  auto_expand_fields: false,
  source_scope: { mode: 'EXPLORATORY', seed_urls: [], source_hints: [] },
  completion_conditions: [],
  advanced_settings: {},
  field_expansion: {},
}

function mountSheet(onChanged = vi.fn()) {
  return mount(CollectionSpecEditorSheet, {
    props: {
      payload: { taskId: '1', expectedVersion: 1, payload: basePayload, onChanged },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CollectionSpecEditorSheet 采集方案编辑器', () => {
  it('修改目标 → 保存草稿调用 updateSpecDraft（不等于确认）', async () => {
    const wrapper = mountSheet()
    await wrapper.find('textarea').setValue('改为：搜集东莞供应商')

    const buttons = wrapper.findAll('button')
    await buttons.find((b) => b.text().includes('保存草稿'))!.trigger('click')
    await flushPromises()

    expect(chatApi.updateSpecDraft).toHaveBeenCalledWith(
      '1',
      expect.objectContaining({ goal: '改为：搜集东莞供应商' }),
    )
    expect(chatApi.confirmSpec).not.toHaveBeenCalled()
  })

  it('确认并执行 → confirmSpec（冻结版本）', async () => {
    const wrapper = mountSheet()
    const buttons = wrapper.findAll('button')
    await buttons.find((b) => b.text().includes('确认并执行'))!.trigger('click')
    await flushPromises()

    expect(chatApi.confirmSpec).toHaveBeenCalledWith(
      '1',
      1,
      expect.objectContaining({ goal: '搜集深圳供应商' }),
    )
  })

  it('添加字段后保存包含新字段', async () => {
    const wrapper = mountSheet()
    const buttons = wrapper.findAll('button')
    await buttons.find((b) => b.text().includes('添加字段'))!.trigger('click')
    await wrapper.findAll('.row__name')[1].setValue('官网')
    await buttons.find((b) => b.text().includes('保存草稿'))!.trigger('click')
    await flushPromises()

    const saved = vi.mocked(chatApi.updateSpecDraft).mock.calls[0][1] as {
      fields: { name: string }[]
    }
    expect(saved.fields.some((f) => f.name === '官网')).toBe(true)
  })
})
