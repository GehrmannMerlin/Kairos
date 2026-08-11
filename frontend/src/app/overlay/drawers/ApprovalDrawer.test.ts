import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import ApprovalDrawer from '@/app/overlay/drawers/ApprovalDrawer.vue'

const mockApproval = {
  approval_id: 1,
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
  getApproval: vi.fn(async () => mockApproval),
  approveApproval: vi.fn(async () => ({ ...mockApproval, state: 'APPROVED' })),
  rejectApproval: vi.fn(async () => ({ ...mockApproval, state: 'REJECTED' })),
  revokeApproval: vi.fn(async () => ({ ...mockApproval, state: 'REVOKED' })),
}))

describe('ApprovalDrawer', () => {
  it('renders masked credential ref and scope label (no cost fields)', async () => {
    const wrapper = mount(ApprovalDrawer, { props: { payload: { approvalId: 1 } } })
    await flushPromises()
    expect(wrapper.text()).toContain('cred-***')
    expect(wrapper.text()).toContain('仅本次动作')
    // D-036：不出现金额/费用
    expect(wrapper.text()).not.toContain('费用')
    expect(wrapper.text()).not.toContain('预算')
  })

  it('revoke button appears only for PENDING/APPROVED', async () => {
    const wrapper = mount(ApprovalDrawer, { props: { payload: { approvalId: 1 } } })
    await flushPromises()
    expect(wrapper.text()).toContain('撤销（未消费）')
  })
})
