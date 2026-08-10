import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/app/error/ApiError'

// EventSource 在 jsdom 不存在；stub 以便 useTaskEvents 可被驱动。
class FakeEventSource {
  onopen: (() => void) | null = null
  onmessage: ((msg: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  // open/close 由测试直接触发
  triggerOpen(): void {
    this.onopen?.()
  }
  triggerMessage(eventType: string): void {
    const data = JSON.stringify({
      event_id: 9,
      event_type: eventType,
      task_id: 1,
      run_id: null,
      occurred_at: '2026-08-10T00:00:00Z',
      payload: {},
    })
    this.onmessage?.({ data } as MessageEvent)
  }
  triggerError(): void {
    this.onerror?.()
  }
}

vi.mock('@/features/tasks/tasks.api', () => ({
  getTask: vi.fn(),
}))

vi.mock('@/features/tasks/commands.api', () => ({
  pauseTask: vi.fn(),
  resumeTask: vi.fn(),
  cancelTask: vi.fn(),
}))

import * as tasksApi from '@/features/tasks/tasks.api'
import * as commandsApi from '@/features/tasks/commands.api'
import TaskStatusDrawer from '@/app/overlay/drawers/TaskStatusDrawer.vue'

function pausingTask() {
  return {
    task_id: 1,
    title: '采集',
    state: 'PAUSING',
    version: 2,
    task_type: null,
    current_spec_version: 1,
    current_plan_version: null,
    template_id: null,
    template_version: null,
    allowed_actions: ['resume', 'cancel'],
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal(
    'EventSource',
    vi.fn().mockImplementation(() => new FakeEventSource()),
  )
})

describe('TaskStatusDrawer', () => {
  it('renders backend state and gates commands by allowed_actions', async () => {
    vi.mocked(tasksApi.getTask).mockResolvedValue(pausingTask())
    const wrapper = mount(TaskStatusDrawer, { props: { payload: { taskId: 1 } } })
    await flushPromises()

    expect(wrapper.text()).toContain('PAUSING') // 真实中间态，非乐观 PAUSED
    const buttons = wrapper.findAll('button')
    const labels = buttons.map((b) => b.text()).join('|')
    expect(labels).toContain('暂停') // 渲染了按钮（disabled 时不可点）
    const pauseBtn = buttons.find((b) => b.text() === '暂停')!
    expect(pauseBtn.attributes('disabled')).toBeDefined() // PAUSING 不允许 pause
    const resumeBtn = buttons.find((b) => b.text() === '恢复')!
    expect(resumeBtn.attributes('disabled')).toBeUndefined()
    const cancelBtn = buttons.find((b) => b.text() === '取消')!
    expect(cancelBtn.attributes('disabled')).toBeUndefined()
  })

  it('resume calls the real command then re-queries truth (no optimistic state)', async () => {
    // useTaskShell 的 immediate watch + Drawer onMounted load() 各触发一次，共 2 次；
    // 命令执行后 load() 再次拉取 → 共 3 次。
    vi.mocked(tasksApi.getTask)
      .mockResolvedValueOnce(pausingTask())
      .mockResolvedValueOnce(pausingTask())
      .mockResolvedValueOnce({
        ...pausingTask(),
        state: 'RUNNING',
        allowed_actions: ['pause', 'cancel'],
      })
    vi.mocked(commandsApi.resumeTask).mockResolvedValue({
      command: 'resume',
      state: 'RUNNING',
      version: 3,
    })
    const wrapper = mount(TaskStatusDrawer, { props: { payload: { taskId: 1 } } })
    await flushPromises()

    const resumeBtn = wrapper.findAll('button').find((b) => b.text() === '恢复')!
    await resumeBtn.trigger('click')
    await flushPromises()

    // 传入真实 expectedVersion（来自 summary.version=2），不写死 0
    expect(commandsApi.resumeTask).toHaveBeenCalledWith('1', { expectedVersion: 2 })
    expect(tasksApi.getTask).toHaveBeenCalledTimes(3) // mount(2) + 命令后 load()
    expect(wrapper.text()).toContain('RUNNING') // UI 以后端事实为准
  })

  it('surfaces command errors without fabricating state', async () => {
    vi.mocked(tasksApi.getTask).mockResolvedValue(pausingTask())
    vi.mocked(commandsApi.cancelTask).mockRejectedValue(
      new ApiError(409, '当前状态不允许取消', 'ILLEGAL_TRANSITION'),
    )
    const wrapper = mount(TaskStatusDrawer, { props: { payload: { taskId: 1 } } })
    await flushPromises()

    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === '取消')!
    await cancelBtn.trigger('click')
    await flushPromises()

    expect(commandsApi.cancelTask).toHaveBeenCalledWith('1', { expectedVersion: 2 })
    expect(wrapper.text()).toContain('当前状态不允许取消')
  })
})
