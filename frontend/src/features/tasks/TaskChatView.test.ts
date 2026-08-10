import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/app/error/ApiError'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { taskId: '1' }, query: {} }),
}))

vi.mock('@/features/tasks/chat.api', () => ({
  getChat: vi.fn(),
  getSpecDraft: vi.fn().mockResolvedValue({ task_id: 1, payload: null }),
  runUnderstanding: vi.fn(),
  sendMessage: vi.fn(),
  addSeedUrl: vi.fn(),
}))

import type { ChatMessageDto } from '@/features/tasks/chat.api'
import * as chatApi from '@/features/tasks/chat.api'
import * as modalStore from '@/app/overlay/modal.store'
import TaskChatView from '@/features/tasks/TaskChatView.vue'

const userMsg: ChatMessageDto = {
  id: 1,
  role: 'user',
  content: '帮我搜集深圳的工业自动化设备供应商',
  ref_type: null,
  ref_id: null,
  meta: null,
  created_at: '2026-08-10T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TaskChatView 对话工作区', () => {
  it('有用户消息且未理解时自动触发目标理解', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValue({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockResolvedValue({
      task_id: 1,
      message: {
        ...userMsg,
        id: 2,
        role: 'assistant' as const,
        ref_type: 'goal_result',
        content: '任务类型：EXPLORATORY',
      },
      result: { task_type: 'EXPLORATORY' },
      spec_draft: { goal: '搜集供应商' },
    })

    mount(TaskChatView)
    await flushPromises()

    expect(chatApi.getChat).toHaveBeenCalledWith('1')
    expect(chatApi.runUnderstanding).toHaveBeenCalledWith('1')
  })

  it('MODEL_NOT_CONFIGURED 打开 Model Required Modal 并携带 returnTo', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValue({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockRejectedValue(
      new ApiError(409, '尚未配置可用的 AI 模型', 'MODEL_NOT_CONFIGURED'),
    )
    const openSpy = vi.spyOn(modalStore, 'openModal')

    mount(TaskChatView)
    await flushPromises()

    expect(openSpy).toHaveBeenCalledWith('MODEL_REQUIRED', { returnTo: '/tasks/1/chat' })
  })

  it('发送消息后追加并重新触发理解（输入不丢）', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValue({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockResolvedValue({
      task_id: 1,
      message: {
        ...userMsg,
        id: 2,
        role: 'assistant' as const,
        ref_type: 'goal_result',
        content: 'ok',
      },
      result: { task_type: 'EXPLORATORY' },
      spec_draft: {},
    })
    vi.mocked(chatApi.sendMessage).mockResolvedValue({
      message: { ...userMsg, id: 3, content: '补充：再加邮箱字段' },
    })

    const wrapper = mount(TaskChatView)
    await flushPromises()

    await wrapper.find('textarea').setValue('补充：再加邮箱字段')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(chatApi.sendMessage).toHaveBeenCalledWith('1', '补充：再加邮箱字段')
  })
})
