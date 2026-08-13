import { apiClient } from '@/app/api/client'

export interface TaskCommandResponse {
  command: 'pause' | 'resume' | 'cancel' | 'delete' | 'restore'
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

/** M-15 软删除（D-065）：非运行任务进入已删除视图。 */
export function deleteTask(
  taskId: string | number,
  input: TaskCommandInput,
): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/delete`, {
    expected_version: input.expectedVersion,
    idempotency_key: input.idempotencyKey,
  })
}

/** M-15 恢复（D-065）：回到软删除前终态。 */
export function restoreTask(
  taskId: string | number,
  input: TaskCommandInput,
): Promise<TaskCommandResponse> {
  return apiClient.post<TaskCommandResponse>(`/tasks/${taskId}/commands/restore`, {
    expected_version: input.expectedVersion,
    idempotency_key: input.idempotencyKey,
  })
}

export interface PermanentDeleteCommand {
  confirmed: boolean
}

/** M-15 永久删除（D-065/D-072）：owner + state==DELETED + 二次强确认。 */
export function permanentDelete(
  taskId: string | number,
  cmd: PermanentDeleteCommand,
): Promise<{ task_id: number }> {
  return apiClient.post<{ task_id: number }>(`/tasks/${taskId}/permanent-delete`, cmd)
}
