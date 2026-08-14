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
  confirmSpec: vi.fn(),
}))

vi.mock('@/features/tasks/tasks.api', () => ({
  getTask: vi.fn().mockResolvedValue({
    task_id: 1,
    title: '采集',
    state: 'DRAFT',
    version: 1,
    task_type: null,
    current_spec_version: null,
    current_plan_version: null,
    allowed_actions: ['submit', 'delete'],
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  }),
}))

vi.mock('@/features/templates/templates.api', () => ({
  createTemplateFromTask: vi.fn().mockResolvedValue({ template_id: 't1', version: 1 }),
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

describe('TaskChatView 客户端超时 vs 服务器事实（竞态修复）', () => {
  const goalMsg: ChatMessageDto = {
    id: 2,
    role: 'assistant',
    content: '任务类型：EXPLORATORY',
    ref_type: 'goal_result',
    ref_id: null,
    meta: null,
    created_at: '2026-08-10T00:00:00Z',
  }

  it('后端已成功但浏览器超时：以服务器结果为准，不显示网络超时错误', async () => {
    vi.mocked(chatApi.getChat)
      .mockResolvedValueOnce({ messages: [userMsg] })
      .mockResolvedValueOnce({ messages: [userMsg, goalMsg] })
    vi.mocked(chatApi.runUnderstanding).mockRejectedValue(
      new ApiError(0, '请求处理时间较长，服务器可能仍在处理中，正在确认结果…', 'CLIENT_TIMEOUT'),
    )

    const wrapper = mount(TaskChatView)
    await flushPromises()

    expect(wrapper.find('.chat__error').exists()).toBe(false)
    expect(wrapper.text()).toContain('任务类型：EXPLORATORY')
    expect(wrapper.text()).toContain('目标理解已完成')
  })

  it('真正网络失败仍显示 NETWORK_ERROR，且不与超时混淆', async () => {
    vi.mocked(chatApi.getChat)
      .mockResolvedValueOnce({ messages: [userMsg] })
      .mockResolvedValueOnce({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockRejectedValue(
      new ApiError(0, '网络连接失败，请稍后重试。', 'CLIENT_NETWORK_ERROR'),
    )

    const wrapper = mount(TaskChatView)
    await flushPromises()

    const errorText = wrapper.find('.chat__error').text()
    expect(errorText).toContain('网络连接失败')
    expect(errorText).not.toContain('目标理解已完成')
  })

  it('客户端超时且服务器尚未持久化：显示确认文案而非 Provider 故障', async () => {
    vi.mocked(chatApi.getChat)
      .mockResolvedValueOnce({ messages: [userMsg] })
      .mockResolvedValueOnce({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockRejectedValue(
      new ApiError(0, '请求处理时间较长，服务器可能仍在处理中，正在确认结果…', 'CLIENT_TIMEOUT'),
    )

    const wrapper = mount(TaskChatView)
    await flushPromises()

    expect(wrapper.find('.chat__error').text()).toContain('请求处理时间较长')
  })

  it('REQUEST_ABORTED 静默处理，不当作失败', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValueOnce({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockRejectedValue(
      new ApiError(0, '请求已取消', 'CLIENT_ABORTED'),
    )

    const wrapper = mount(TaskChatView)
    await flushPromises()

    expect(wrapper.find('.chat__error').exists()).toBe(false)
  })

  it('已有 goal_result 时不再自动重复触发理解', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValueOnce({ messages: [userMsg, goalMsg] })

    mount(TaskChatView)
    await flushPromises()

    expect(chatApi.runUnderstanding).not.toHaveBeenCalled()
  })
})
