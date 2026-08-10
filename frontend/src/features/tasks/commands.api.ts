import { apiClient } from '@/app/api/client'

export interface TaskCommandResponse {
  command: 'pause' | 'resume' | 'cancel'
  state: string
  version: number
}

export interface TaskCommandInput {
  expectedVersion: number
  idempotencyKey?: string
}

export function pauseTask(
  taskId: string | number,
  input: TaskCommandInput,
): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/pause`, {
    expected_version: input.expectedVersion,
    idempotency_key: input.idempotencyKey,
  })
}

export function resumeTask(
  taskId: string | number,
  input: TaskCommandInput,
): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/resume`, {
    expected_version: input.expectedVersion,
    idempotency_key: input.idempotencyKey,
  })
}

export function cancelTask(
  taskId: string | number,
  input: TaskCommandInput,
): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/cancel`, {
    expected_version: input.expectedVersion,
    idempotency_key: input.idempotencyKey,
  })
}
