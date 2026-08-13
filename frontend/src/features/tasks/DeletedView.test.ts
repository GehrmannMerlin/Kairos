import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DeleteConfirmModal from '@/app/overlay/modals/DeleteConfirmModal.vue'

const api = vi.hoisted(() => ({
  deleteTask: vi.fn(),
  restoreTask: vi.fn(),
  permanentDelete: vi.fn(),
  listTasks: vi.fn(),
  openModal: vi.fn(),
  closeModal: vi.fn(),
  routeQuery: {} as Record<string, string>,
}))

vi.mock('@/features/tasks/commands.api', () => ({
  deleteTask: api.deleteTask,
  restoreTask: api.restoreTask,
  permanentDelete: api.permanentDelete,
}))
vi.mock('@/features/tasks/tasks.api', () => ({ listTasks: api.listTasks }))
vi.mock('@/app/overlay/modal.store', () => ({
  openModal: api.openModal,
  closeModal: api.closeModal,
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: {}, query: api.routeQuery }),
  RouterLink: { template: '<a><slot /></a>' },
}))

describe('DeleteConfirmModal 两段确认', () => {
  beforeEach(() => {
    api.deleteTask.mockReset().mockResolvedValue({ command: 'delete', state: 'DELETED', version: 2 })
    api.permanentDelete.mockReset().mockResolvedValue({ task_id: 7 })
    api.closeModal.mockClear()
  })

  it('soft 删除单段确认 → deleteTask 带 expected_version', async () => {
    const wrapper = mount(DeleteConfirmModal, {
      props: { payload: { taskId: 7, version: 1, action: 'soft', onDone: vi.fn() } },
    })
    await wrapper.find('[data-testid="delete-confirm"]').trigger('click')
    await flushPromises()
    expect(api.deleteTask).toHaveBeenCalledWith(7, { expectedVersion: 1 })
    expect(api.closeModal).toHaveBeenCalled()
  })

  it('permanent 删除两段确认后才调 permanent-delete', async () => {
    const wrapper = mount(DeleteConfirmModal, {
      props: { payload: { taskId: 7, version: 1, action: 'permanent', onDone: vi.fn() } },
    })
    // 第一段：仅进入第二段，不调用 API
    await wrapper.find('[data-testid="permanent-step1"]').trigger('click')
    expect(api.permanentDelete).not.toHaveBeenCalled()
    // 第二段：确认 → 调用 permanent-delete
    await wrapper.find('[data-testid="delete-confirm"]').trigger('click')
    await flushPromises()
    expect(api.permanentDelete).toHaveBeenCalledWith(7, { confirmed: true })
  })
})

describe('TasksView 已删除视图', () => {
  const DELETED_TASK = {
    task_id: 7,
    title: '已删任务',
    state: 'DELETED',
    version: 3,
    task_type: null,
    current_spec_version: null,
    current_plan_version: null,
    allowed_actions: ['restore'],
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  }

  beforeEach(() => {
    api.listTasks.mockReset()
    api.restoreTask.mockReset().mockResolvedValue({ command: 'restore', state: 'COMPLETED', version: 4 })
    api.openModal.mockClear()
  })

  it('已删除视图展示 恢复/永久删除，恢复调用 restore API', async () => {
    api.routeQuery.view = 'deleted'
    api.listTasks.mockResolvedValue({ tasks: [DELETED_TASK] })
    const { default: TasksView } = await import('@/features/tasks/TasksView.vue')
    const wrapper = mount(TasksView)
    await flushPromises()
    expect(api.listTasks).toHaveBeenCalledWith({ view: 'deleted' })
    expect(wrapper.find('[data-testid="restore"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="permanent-delete"]').exists()).toBe(true)
    await wrapper.find('[data-testid="restore"]').trigger('click')
    await flushPromises()
    expect(api.restoreTask).toHaveBeenCalledWith(7, { expectedVersion: 3 })
  })

  it('正常列表 删除 按钮打开 DELETE_CONFIRM soft modal', async () => {
    api.routeQuery.view = ''
    api.listTasks.mockResolvedValue({
      tasks: [
        { ...DELETED_TASK, task_id: 9, state: 'COMPLETED', allowed_actions: ['delete'] },
      ],
    })
    const { default: TasksView } = await import('@/features/tasks/TasksView.vue')
    const wrapper = mount(TasksView)
    await flushPromises()
    expect(wrapper.find('[data-testid="soft-delete"]').exists()).toBe(true)
    await wrapper.find('[data-testid="soft-delete"]').trigger('click')
    expect(api.openModal).toHaveBeenCalledWith('DELETE_CONFIRM', {
      taskId: 9,
      version: 3,
      action: 'soft',
      onDone: expect.any(Function),
    })
  })
})
