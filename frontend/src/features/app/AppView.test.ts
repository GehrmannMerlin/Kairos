import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/features/tasks/chat.api', () => ({
  createTaskDraft: vi.fn(),
}))

vi.mock('@/features/tasks/tasks.api', () => ({
  listTasks: vi.fn().mockResolvedValue({ tasks: [] }),
}))

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRouter: () => ({ push: pushMock }),
}))

import * as chatApi from '@/features/tasks/chat.api'
import AppView from '@/features/app/AppView.vue'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AppView 工作台', () => {
  it('自然语言输入 → createTaskDraft → 进入 /tasks/:id/chat', async () => {
    vi.mocked(chatApi.createTaskDraft).mockResolvedValue({ task_id: 7 })
    const wrapper = mount(AppView)

    await wrapper.find('textarea').setValue('帮我搜集深圳的工业自动化设备供应商')
    await wrapper.find('.workbench__actions button').trigger('click')
    await flushPromises()

    expect(chatApi.createTaskDraft).toHaveBeenCalledWith(
      expect.objectContaining({ content: '帮我搜集深圳的工业自动化设备供应商' }),
    )
    expect(pushMock).toHaveBeenCalledWith('/tasks/7/chat')
  })

  it('只添加网址也能创建任务并保留 URL 到 Draft Context', async () => {
    vi.mocked(chatApi.createTaskDraft).mockResolvedValue({ task_id: 8 })
    const wrapper = mount(AppView)

    await wrapper.find('.workbench__url').setValue('https://example.com/suppliers')
    await wrapper.find('.workbench__urlrow .ghost').trigger('click')
    await wrapper.find('.workbench__actions button').trigger('click')
    await flushPromises()

    expect(chatApi.createTaskDraft).toHaveBeenCalledWith(
      expect.objectContaining({ seed_urls: ['https://example.com/suppliers'] }),
    )
    expect(pushMock).toHaveBeenCalledWith('/tasks/8/chat')
  })
})
