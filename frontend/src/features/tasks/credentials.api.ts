import { apiClient } from '@/app/api/client'

/** 网站凭据 API（M-10 / D-059）。DTO 只有脱敏 metadata，永不回读明文。 */
export type WebsiteCredentialType = 'cookie' | 'username_password'

export interface WebsiteCredentialDto {
  credential_id: number
  type: WebsiteCredentialType
  domain: string | null
  scope: 'CURRENT_TASK' | 'SAVED_DOMAIN' | null
  task_id: number | null
  masked: string
  created_at: string | null
}

export interface StoreCredentialCommand {
  type: WebsiteCredentialType
  payload: Record<string, unknown>
  scope: 'CURRENT_TASK' | 'SAVED_DOMAIN'
  domain: string
  from_saved_credential_id?: number | null
}

export interface StoreCredentialResult {
  credential: WebsiteCredentialDto
  approval_id: number | null
}

export function storeTaskCredential(
  taskId: string | number,
  cmd: StoreCredentialCommand,
): Promise<StoreCredentialResult> {
  return apiClient.post<StoreCredentialResult>(`/tasks/${taskId}/credentials`, cmd)
}

export function listTaskCredentials(
  taskId: string | number,
): Promise<{ credentials: WebsiteCredentialDto[] }> {
  return apiClient.get(`/tasks/${taskId}/credentials`)
}

export function deleteTaskCredential(
  taskId: string | number,
  credentialId: number,
): Promise<{ ok: boolean }> {
  return apiClient.delete(`/tasks/${taskId}/credentials/${credentialId}`)
}

export function listSavedCredentials(): Promise<{ credentials: WebsiteCredentialDto[] }> {
  return apiClient.get('/credentials/saved')
}

export function deleteSavedCredential(credentialId: number): Promise<{ ok: boolean }> {
  return apiClient.delete(`/credentials/${credentialId}`)
}
