import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CredentialDrawer from '@/app/overlay/drawers/CredentialDrawer.vue'

const mocks = vi.hoisted(() => ({
  openDrawer: vi.fn(),
  storeTaskCredential: vi.fn(),
}))

vi.mock('@/app/overlay/drawer.store', () => ({ openDrawer: mocks.openDrawer }))
vi.mock('@/features/tasks/credentials.api', () => ({
  storeTaskCredential: mocks.storeTaskCredential,
}))

describe('CredentialDrawer', () => {
  beforeEach(() => {
    mocks.openDrawer.mockReset()
    mocks.storeTaskCredential.mockReset()
    mocks.storeTaskCredential.mockResolvedValue({
      credential: { credential_id: 1, masked: 'cred-****0001' },
      approval_id: 42,
    })
  })

  it('cookie credential submits scope/domain and opens Approval drawer', async () => {
    const wrapper = mount(CredentialDrawer, {
      props: { payload: { taskId: 7, domain: 'example.com' } },
    })
    // 填 Cookie 行
    wrapper.findAll('input[data-test="cookie-name"]')[0].setValue('session')
    wrapper.findAll('input[data-test="cookie-value"]')[0].setValue('SECRET_VALUE')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const command = mocks.storeTaskCredential.mock.calls[0][1]
    expect(command.type).toBe('cookie')
    expect(command.scope).toBe('CURRENT_TASK')
    expect(command.domain).toBe('example.com')
    expect(command.payload.cookies[0].name).toBe('session')
    // 保存后打开 Approval Drawer（凭据访问必须审批，D-017）
    expect(mocks.openDrawer).toHaveBeenCalledWith('APPROVAL', { approvalId: 42 })
  })

  it('username/password payload carries username and password', async () => {
    const wrapper = mount(CredentialDrawer, {
      props: { payload: { taskId: 7, domain: 'example.com' } },
    })
    const typeSelect = wrapper.find('select[data-test="type"]')
    await typeSelect.setValue('username_password')
    wrapper.find('input[data-test="username"]').setValue('kairos')
    wrapper.find('input[data-test="password"]').setValue('p@ss')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const command = mocks.storeTaskCredential.mock.calls[0][1]
    expect(command.type).toBe('username_password')
    expect(command.payload).toEqual({ username: 'kairos', password: 'p@ss' })
  })
})
