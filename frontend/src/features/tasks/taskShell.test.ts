import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { ApiError } from '@/app/error/ApiError'

vi.mock('@/features/tasks/tasks.api', () => ({
  getTask: vi.fn(),
  listTasks: vi.fn(),
}))

import * as tasksApi from '@/features/tasks/tasks.api'
import { useTaskShell } from '@/features/tasks/useTaskShell'

const okTask = {
  task_id: 1,
  title: '采集深圳供应商',
  state: 'DRAFT',
  version: 1,
  task_type: 'directed',
  current_spec_version: null,
  current_plan_version: null,
  allowed_actions: ['submit', 'delete'],
  created_at: '2026-08-10T00:00:00Z',
  updated_at: '2026-08-10T00:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useTaskShell', () => {
  it('loads an owner-safe task shell from the backend', async () => {
    vi.mocked(tasksApi.getTask).mockResolvedValue(okTask)
    const taskId = ref('1')
    const shell = useTaskShell(taskId)
    await flushPromises()

    expect(shell.summary.value?.title).toBe('采集深圳供应商')
    expect(shell.state.value).toBe('DRAFT')
    expect(shell.notFound.value).toBe(false)
    // allowed_actions 来自后端，前端不做本地状态猜测。
    expect(shell.can('delete')).toBe(true)
    expect(shell.can('pause')).toBe(false)
    expect(tasksApi.getTask).toHaveBeenCalledWith('1')
  })

  it('marks unauthorized tasks as not-found without leaking metadata', async () => {
    vi.mocked(tasksApi.getTask).mockRejectedValue(new ApiError(404, '资源不存在', 'NOT_FOUND'))
    const taskId = ref('999')
    const shell = useTaskShell(taskId)
    await flushPromises()

    expect(shell.notFound.value).toBe(true)
    expect(shell.summary.value).toBeNull()
    expect(shell.state.value).toBeNull()
    expect(shell.allowedActions.value).toEqual([])
    expect(shell.can('submit')).toBe(false)
  })

  it('surfaces non-404 errors without fabricating data', async () => {
    vi.mocked(tasksApi.getTask).mockRejectedValue(new ApiError(503, '服务暂不可用'))
    const taskId = ref('1')
    const shell = useTaskShell(taskId)
    await flushPromises()

    expect(shell.notFound.value).toBe(false)
    expect(shell.error.value).not.toBeNull()
    expect(shell.summary.value).toBeNull()
  })
})
