import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

vi.mock('@/features/tasks/plans.api', () => ({
  generatePlan: vi.fn(),
  getPlanSummary: vi.fn(),
  startPlan: vi.fn(),
}))

vi.mock('@/features/execution/ExecutionProgressPanel.vue', () => ({
  default: {
    props: ['taskId'],
    template: '<div data-testid="execution-progress-panel" />',
  },
}))

vi.mock('@/features/templates/templates.api', () => ({
  createTemplateFromTask: vi.fn().mockResolvedValue({ template_id: 't1', version: 1 }),
}))

import type { ChatMessageDto, UnderstandDto } from '@/features/tasks/chat.api'
import * as chatApi from '@/features/tasks/chat.api'
import * as modalStore from '@/app/overlay/modal.store'
import * as plansApi from '@/features/tasks/plans.api'
import type { SpecDraftPayload } from '@/features/tasks/spec.types'
import * as tasksApi from '@/features/tasks/tasks.api'
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

const goalMsg: ChatMessageDto = {
  id: 2,
  role: 'assistant',
  content: '任务类型：EXPLORATORY',
  ref_type: 'goal_result',
  ref_id: null,
  meta: null,
  created_at: '2026-08-10T00:00:00Z',
}

function successDto(overrides: Partial<UnderstandDto> = {}): UnderstandDto {
  return {
    task_id: 1,
    status: 'SUCCEEDED',
    message: { ...goalMsg },
    result: { task_type: 'EXPLORATORY' },
    spec_draft: { goal: '搜集深圳的工业自动化设备供应商' },
    attempt_id: 1,
    trigger_source: 'AUTO_INITIAL',
    ...overrides,
  }
}

const specDraft: SpecDraftPayload = {
  schema_version: 'm06.1',
  task_type: 'SPECIFIED_SOURCE',
  task_name: '采集',
  goal: '抓取网站',
  fields: [],
  auto_expand_fields: false,
  source_scope: { mode: 'SPECIFIED_SOURCE', seed_urls: ['https://example.com'], source_hints: [] },
  completion_conditions: [],
  advanced_settings: {},
  field_expansion: {},
}

function taskShell(overrides: Record<string, unknown> = {}) {
  return {
    task_id: 1,
    title: '采集',
    state: 'DRAFT',
    version: 1,
    task_type: null,
    current_spec_version: null,
    current_plan_version: null,
    template_id: null,
    template_version: null,
    allowed_actions: ['submit', 'delete'],
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
    ...overrides,
  }
}

function planSummary(overrides: Record<string, unknown> = {}) {
  return {
    task_id: 1,
    plan_version: 1,
    spec_version: 1,
    validation_status: 'VALID',
    plan_fingerprint: 'fp-1',
    node_count: 1,
    node_types: ['HTTP_FETCH'],
    diff_summary: null,
    trigger_reason: null,
    run_id: null,
    run_state: null,
    start_recoverable: false,
    validator_issues: [],
    created_at: '2026-08-10T00:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(chatApi.getSpecDraft).mockResolvedValue({ task_id: 1, payload: null })
  vi.mocked(tasksApi.getTask).mockResolvedValue(taskShell())
  vi.mocked(plansApi.getPlanSummary).mockResolvedValue(planSummary())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('TaskChatView 对话工作区', () => {
  it('有用户消息且未理解时自动触发目标理解（AUTO_INITIAL）', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValue({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockResolvedValue(successDto())

    mount(TaskChatView)
    await flushPromises()

    expect(chatApi.getChat).toHaveBeenCalledWith('1')
    expect(chatApi.runUnderstanding).toHaveBeenCalledWith('1', 'AUTO_INITIAL')
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

  it('发送消息后追加并重新触发理解（USER_SEND），输入不丢', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValue({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockResolvedValue(successDto())
    vi.mocked(chatApi.sendMessage).mockResolvedValue({
      message: { ...userMsg, id: 3, content: '补充：再加邮箱字段' },
    })

    const wrapper = mount(TaskChatView)
    await flushPromises()
    vi.mocked(chatApi.runUnderstanding).mockClear()

    await wrapper.find('textarea').setValue('补充：再加邮箱字段')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(chatApi.sendMessage).toHaveBeenCalledWith('1', '补充：再加邮箱字段')
    expect(chatApi.runUnderstanding).toHaveBeenCalledWith('1', 'USER_SEND')
  })

  it('已有 goal_result 时不再自动重复触发理解', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValueOnce({ messages: [userMsg, goalMsg] })

    mount(TaskChatView)
    await flushPromises()

    expect(chatApi.runUnderstanding).not.toHaveBeenCalled()
  })
})

describe('TaskChatView AI 请求生命周期', () => {
  it('点「重新理解」显式触发 USER_REUNDERSTAND', async () => {
    vi.mocked(chatApi.getChat).mockResolvedValue({ messages: [userMsg, goalMsg] })
    vi.mocked(chatApi.runUnderstanding).mockResolvedValue(successDto())

    const wrapper = mount(TaskChatView)
    await flushPromises()

    const reunderstand = wrapper.findAll('button').find((b) => b.text() === '重新理解')
    expect(reunderstand).toBeTruthy()
    await reunderstand!.trigger('click')
    await flushPromises()

    expect(chatApi.runUnderstanding).toHaveBeenCalledWith('1', 'USER_REUNDERSTAND')
  })

  it('ALREADY_SUCCEEDED 复用服务器结果，不显示错误', async () => {
    vi.mocked(chatApi.getChat)
      .mockResolvedValueOnce({ messages: [userMsg] })
      .mockResolvedValueOnce({ messages: [userMsg, goalMsg] })
    vi.mocked(chatApi.runUnderstanding).mockResolvedValue(
      successDto({ status: 'ALREADY_SUCCEEDED' }),
    )

    const wrapper = mount(TaskChatView)
    await flushPromises()

    expect(wrapper.find('.chat__error').exists()).toBe(false)
    expect(wrapper.text()).toContain('任务类型：EXPLORATORY')
  })

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

  it('真正网络失败仍显示网络错误，且不与超时混淆', async () => {
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

  it('客户端超时且服务器尚未持久化：温和提示，不伪装成 Provider 故障', async () => {
    vi.useFakeTimers()
    vi.mocked(chatApi.getChat)
      .mockResolvedValueOnce({ messages: [userMsg] })
      .mockResolvedValue({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockRejectedValue(
      new ApiError(0, '请求处理时间较长，服务器可能仍在处理中，正在确认结果…', 'CLIENT_TIMEOUT'),
    )

    const wrapper = mount(TaskChatView)
    // 跑完整个有界 reconcile 窗口（40 × 3s = 120s）仍无服务器事实。
    await vi.advanceTimersByTimeAsync(120_000 + 100)

    expect(wrapper.find('.chat__error').exists()).toBe(false)
    expect(wrapper.text()).toContain('模型仍在处理中')
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

  it('IN_PROGRESS（另一 attempt 在途）时轮询直到服务器落库，不重复调用', async () => {
    vi.useFakeTimers()
    // loadChat 第一次；reconcile 第 1 次仍未落库；第 2 次落库。
    vi.mocked(chatApi.getChat)
      .mockResolvedValueOnce({ messages: [userMsg] })
      .mockResolvedValueOnce({ messages: [userMsg] })
      .mockResolvedValue({ messages: [userMsg, goalMsg] })
    vi.mocked(chatApi.runUnderstanding).mockResolvedValue(successDto({ status: 'IN_PROGRESS' }))

    const wrapper = mount(TaskChatView)
    await vi.advanceTimersByTimeAsync(0) // 初始 loadChat + runUnderstanding(IN_PROGRESS) + 第一次轮询
    await vi.advanceTimersByTimeAsync(3_000) // 第二次轮询：goal_result 落库

    expect(wrapper.find('.chat__error').exists()).toBe(false)
    expect(wrapper.text()).toContain('任务类型：EXPLORATORY')
    expect(chatApi.runUnderstanding).toHaveBeenCalledTimes(1)
  })

  it('慢模型：10 秒后仍保持工作状态，显示「模型仍在处理中」而非卡死', async () => {
    vi.useFakeTimers()
    vi.mocked(chatApi.getChat).mockResolvedValueOnce({ messages: [userMsg] })
    vi.mocked(chatApi.runUnderstanding).mockImplementation(() => new Promise(() => {})) // 挂起

    const wrapper = mount(TaskChatView)
    await vi.advanceTimersByTimeAsync(0)

    expect(wrapper.text()).toContain('模型正在理解任务…')
    expect(wrapper.find('.chat__error').exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.text()).toContain('模型仍在处理中，复杂任务可能需要更长时间…')
    // 理解中按钮仍处于禁用状态
    const reunderstand = wrapper.findAll('button').find((b) => b.text().includes('理解中'))
    expect(reunderstand?.attributes('disabled')).toBeDefined()
  })
})

describe('TaskChatView Plan 生命周期恢复', () => {
  async function mountConfirmedDraft() {
    vi.mocked(chatApi.getChat).mockResolvedValue({ messages: [userMsg, goalMsg] })
    vi.mocked(chatApi.getSpecDraft).mockResolvedValue({ task_id: 1, payload: { ...specDraft } })
    vi.mocked(chatApi.confirmSpec).mockResolvedValue({
      task_id: 1,
      spec_version: 1,
      state: 'QUEUED',
    })

    const wrapper = mount(TaskChatView)
    await flushPromises()
    const confirm = wrapper.findAll('button').find((button) => button.text() === '确认并执行')
    expect(confirm).toBeTruthy()
    await confirm!.trigger('click')
    return wrapper
  }

  it('Provider 超时只刷新一次服务端状态，再提供显式重试生成', async () => {
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(taskShell())
      .mockResolvedValueOnce(taskShell({ version: 2, current_spec_version: 1 }))
      .mockResolvedValue(taskShell({ version: 2, current_spec_version: 1 }))
    vi.mocked(plansApi.generatePlan).mockRejectedValue(
      new ApiError(504, 'provider timeout during read', 'PROVIDER_TIMEOUT'),
    )

    const wrapper = await mountConfirmedDraft()
    await flushPromises()

    expect(tasksApi.getTask).toHaveBeenCalledTimes(3)
    expect(plansApi.generatePlan).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('模型服务响应超时')
    expect(wrapper.findAll('button').some((button) => button.text() === '重试生成')).toBe(true)
  })

  it('执行就绪检查阻塞时显示服务器首条安全说明，不提供启动重试', async () => {
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(taskShell())
      .mockResolvedValueOnce(taskShell({ version: 2, current_spec_version: 1 }))
      .mockResolvedValue(
        taskShell({
          version: 3,
          current_spec_version: 1,
          current_plan_version: 1,
        }),
      )
    vi.mocked(plansApi.getPlanSummary).mockResolvedValue(
      planSummary({
        preflight_status: 'BLOCKED',
        preflight_issues: [
          {
            code: 'EXECUTION_INPUT_UNMATERIALIZABLE',
            safe_message: '冻结的来源输入无法直接执行。',
          },
        ],
      }),
    )
    vi.mocked(plansApi.generatePlan).mockRejectedValue(
      new ApiError(409, '冻结的来源输入无法直接执行。', 'EXECUTION_PREFLIGHT_BLOCKED'),
    )

    const wrapper = await mountConfirmedDraft()
    await flushPromises()

    expect(tasksApi.getTask).toHaveBeenCalledTimes(3)
    expect(plansApi.getPlanSummary).toHaveBeenCalledWith('1', 1)
    expect(wrapper.find('[data-test="plan-summary"]').exists()).toBe(true)
    expect(wrapper.find('.chat__error').text()).toContain('冻结的来源输入无法直接执行。')
    expect(wrapper.find('.chat__notice').exists()).toBe(false)
    expect(wrapper.findAll('button').some((button) => button.text() === '重试启动')).toBe(false)
  })

  it('启动失败保留已持久化计划，重试动作只调用 start-only endpoint', async () => {
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(taskShell())
      .mockResolvedValueOnce(taskShell({ version: 2, current_spec_version: 1 }))
      .mockResolvedValue(
        taskShell({
          version: 3,
          current_spec_version: 1,
          current_plan_version: 1,
        }),
      )
    vi.mocked(plansApi.getPlanSummary).mockResolvedValue(
      planSummary({
        run_id: 7,
        run_state: 'pending',
        start_recoverable: true,
        validator_issues: [{ code: 'SPEC_SCOPE_EXPANSION', node_id: 'fetch-1' }],
      }),
    )
    vi.mocked(plansApi.generatePlan).mockRejectedValue(
      new ApiError(503, '计划已保存，但工作流服务暂时不可用', 'PLAN_START_FAILED'),
    )
    vi.mocked(plansApi.startPlan).mockResolvedValue({
      task_id: 1,
      plan_version: 1,
      validation_status: 'VALID',
      node_count: 1,
      run_id: 7,
      workflow_id: 'task-workflow-1',
      run_state: 'pending',
      start_recoverable: false,
      validator_issues: [{ code: 'SPEC_SCOPE_EXPANSION', node_id: 'fetch-1' }],
    })

    const wrapper = await mountConfirmedDraft()
    await flushPromises()

    expect(wrapper.find('[data-test="plan-summary"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('SPEC_SCOPE_EXPANSION')
    const retryStart = wrapper.findAll('button').find((button) => button.text() === '重试启动')
    expect(retryStart).toBeTruthy()

    await retryStart!.trigger('click')
    await flushPromises()

    expect(plansApi.startPlan).toHaveBeenCalledWith('1', 1, expect.any(AbortSignal))
    expect(plansApi.generatePlan).toHaveBeenCalledTimes(1)
  })

  it('启动重试被执行就绪检查阻塞时刷新摘要并清除重试动作', async () => {
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(taskShell())
      .mockResolvedValueOnce(taskShell({ version: 2, current_spec_version: 1 }))
      .mockResolvedValue(
        taskShell({
          version: 3,
          current_spec_version: 1,
          current_plan_version: 1,
        }),
      )
    vi.mocked(plansApi.getPlanSummary)
      .mockResolvedValueOnce(
        planSummary({
          run_id: 7,
          run_state: 'pending',
          start_recoverable: true,
        }),
      )
      .mockResolvedValueOnce(
        planSummary({
          preflight_status: 'BLOCKED',
          preflight_issues: [
            {
              code: 'EXECUTION_INPUT_UNMATERIALIZABLE',
              safe_message: '冻结的来源输入无法直接执行。',
            },
          ],
        }),
      )
    vi.mocked(plansApi.generatePlan).mockRejectedValue(
      new ApiError(503, '计划已保存，但工作流服务暂时不可用', 'PLAN_START_FAILED'),
    )
    vi.mocked(plansApi.startPlan).mockRejectedValue(
      new ApiError(409, '冻结的来源输入无法直接执行。', 'EXECUTION_PREFLIGHT_BLOCKED'),
    )

    const wrapper = await mountConfirmedDraft()
    await flushPromises()

    const retryStart = wrapper.findAll('button').find((button) => button.text() === '重试启动')
    expect(retryStart).toBeTruthy()

    await retryStart!.trigger('click')
    await flushPromises()

    expect(plansApi.startPlan).toHaveBeenCalledWith('1', 1, expect.any(AbortSignal))
    expect(tasksApi.getTask).toHaveBeenCalledTimes(4)
    expect(plansApi.getPlanSummary).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.chat__error').text()).toContain('冻结的来源输入无法直接执行。')
    expect(wrapper.findAll('button').some((button) => button.text() === '重试启动')).toBe(false)
  })

  it('模糊网络结果每三秒轮询且最多 45 次，绝不自动重新生成', async () => {
    vi.useFakeTimers()
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(taskShell())
      .mockResolvedValue(taskShell({ version: 2, current_spec_version: 1 }))
    vi.mocked(plansApi.generatePlan).mockRejectedValue(
      new ApiError(0, '网络连接失败，请稍后重试。', 'CLIENT_NETWORK_ERROR'),
    )

    const wrapperPromise = mountConfirmedDraft()
    await vi.advanceTimersByTimeAsync(0)
    const wrapper = await wrapperPromise
    await vi.advanceTimersByTimeAsync(135_001)

    expect(tasksApi.getTask).toHaveBeenCalledTimes(47)
    expect(plansApi.generatePlan).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('未确认计划结果')
  })

  it('轮询发现计划版本推进后立即停止', async () => {
    vi.useFakeTimers()
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(taskShell())
      .mockResolvedValueOnce(taskShell({ version: 2, current_spec_version: 1 }))
      .mockResolvedValue(
        taskShell({
          version: 3,
          current_spec_version: 1,
          current_plan_version: 1,
        }),
      )
    vi.mocked(plansApi.getPlanSummary).mockResolvedValue(
      planSummary({ run_id: 8, run_state: 'pending' }),
    )
    vi.mocked(plansApi.generatePlan).mockRejectedValue(
      new ApiError(0, '网络连接失败，请稍后重试。', 'CLIENT_NETWORK_ERROR'),
    )

    const wrapperPromise = mountConfirmedDraft()
    await vi.advanceTimersByTimeAsync(0)
    const wrapper = await wrapperPromise
    await vi.advanceTimersByTimeAsync(3_001)
    await vi.advanceTimersByTimeAsync(30_000)

    expect(tasksApi.getTask).toHaveBeenCalledTimes(3)
    expect(wrapper.find('[data-test="plan-summary"]').exists()).toBe(true)
    expect(plansApi.generatePlan).toHaveBeenCalledTimes(1)
  })

  it('卸载时中止在途生成请求并停止后续轮询', async () => {
    vi.useFakeTimers()
    let requestSignal: AbortSignal | undefined
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(taskShell())
      .mockResolvedValue(taskShell({ version: 2, current_spec_version: 1 }))
    vi.mocked(plansApi.generatePlan).mockImplementation((_taskId, _command, signal) => {
      requestSignal = signal
      return new Promise(() => {})
    })

    const wrapperPromise = mountConfirmedDraft()
    await vi.advanceTimersByTimeAsync(0)
    const wrapper = await wrapperPromise
    expect(requestSignal?.aborted).toBe(false)

    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(135_001)

    expect(requestSignal?.aborted).toBe(true)
    expect(tasksApi.getTask).toHaveBeenCalledTimes(2)
  })
})
