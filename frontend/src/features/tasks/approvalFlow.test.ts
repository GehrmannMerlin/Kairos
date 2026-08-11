import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ApprovalDrawer from '@/app/overlay/drawers/ApprovalDrawer.vue'
import { openDrawer } from '@/app/overlay/drawer.store'
import { parseTaskQuery } from '@/app/router/deepLinks'
import PlanSummaryCard from '@/features/tasks/PlanSummaryCard.vue'

const mockApproval = {
  approval_id: 42,
  task_id: 1,
  state: 'PENDING',
  action_type: 'fetch_non_public',
  node_id: 'n1',
  node_type: 'fetch',
  target: 'https://example.com/private/{id}',
  reason: '访问非公开页面',
  approved_scope: 'this_action',
  credential_ref: { kind: 'website', masked: 'cred-***' },
  status_payload: null,
  expires_at: null,
  created_at: '2026-08-11T00:00:00Z',
}

vi.mock('@/features/tasks/approvals.api', () => ({
  getApproval: vi.fn(async (id: string | number) => ({ ...mockApproval, approval_id: Number(id) })),
  approveApproval: vi.fn(async () => ({ ...mockApproval, state: 'APPROVED' })),
  rejectApproval: vi.fn(async () => ({ ...mockApproval, state: 'REJECTED' })),
  revokeApproval: vi.fn(async () => ({ ...mockApproval, state: 'REVOKED' })),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('M-08 approval UI', () => {
  it('deep link ?approval= opens the same Approval Drawer', async () => {
    const query = parseTaskQuery({ approval: '42' })
    expect(query.approval).toBe('42')
    openDrawer('APPROVAL', { approvalId: query.approval })

    const wrapper = mount(ApprovalDrawer, { props: { payload: { approvalId: '42' } } })
    await flushPromises()
    expect(wrapper.text()).toContain('访问非公开页面')
    expect(wrapper.text()).toContain('PENDING')
  })

  it('low-risk VALID plan has no second confirmation button', () => {
    const wrapper = mount(PlanSummaryCard, {
      props: {
        summary: {
          task_id: 1,
          plan_version: 1,
          spec_version: 1,
          validation_status: 'VALID',
          plan_fingerprint: 'fp',
          node_count: 3,
          node_types: ['fetch', 'extract', 'generate_artifact'],
          diff_summary: null,
          trigger_reason: null,
          created_at: '2026-08-11T00:00:00Z',
        },
      },
    })
    expect(wrapper.find('button[data-test="confirm-plan"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('低风险，自动执行')
  })

  it('approve reflects real backend state', async () => {
    const wrapper = mount(ApprovalDrawer, { props: { payload: { approvalId: '7' } } })
    await flushPromises()
    expect(wrapper.text()).toContain('PENDING')
    await wrapper.find('button[data-test="approve"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('APPROVED')
  })

  it('reject reflects real backend state', async () => {
    const wrapper = mount(ApprovalDrawer, { props: { payload: { approvalId: '8' } } })
    await flushPromises()
    await wrapper.find('button[data-test="reject"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('REJECTED')
  })
})
