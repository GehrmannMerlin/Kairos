import { apiClient } from '@/app/api/client'

/** 真实 Approval 对象（M-08 / D-017）。Approval DB Object 是事实，不是前端伪对象。 */
export interface ApprovalDto {
  approval_id: number
  task_id: number
  state: string
  action_type: string
  node_id: string | null
  node_type: string | null
  target: string | null
  reason: string | null
  approved_scope: string
  credential_ref: Record<string, unknown> | null
  status_payload: Record<string, unknown> | null
  expires_at: string | null
  created_at: string
}

export interface ApprovalListDto {
  task_id: number
  approvals: ApprovalDto[]
}

export interface ApprovalResolutionCommand {
  expected_version: number
}

export function getApproval(approvalId: string | number): Promise<ApprovalDto> {
  return apiClient.get<ApprovalDto>(`/approvals/${approvalId}`)
}

export function listTaskApprovals(taskId: string | number): Promise<ApprovalListDto> {
  return apiClient.get<ApprovalListDto>(`/tasks/${taskId}/approvals`)
}

export function listPendingTaskApprovals(taskId: string | number): Promise<ApprovalListDto> {
  return apiClient.get<ApprovalListDto>(`/tasks/${taskId}/approvals/pending`)
}

export function approveApproval(
  approvalId: string | number,
  cmd: ApprovalResolutionCommand,
): Promise<ApprovalDto> {
  return apiClient.post<ApprovalDto>(`/approvals/${approvalId}/approve`, cmd)
}

export function rejectApproval(
  approvalId: string | number,
  cmd: ApprovalResolutionCommand,
): Promise<ApprovalDto> {
  return apiClient.post<ApprovalDto>(`/approvals/${approvalId}/reject`, cmd)
}

export function revokeApproval(
  approvalId: string | number,
  cmd: ApprovalResolutionCommand,
): Promise<ApprovalDto> {
  return apiClient.post<ApprovalDto>(`/approvals/${approvalId}/revoke`, cmd)
}
