import { AI_REQUEST_TIMEOUT_MS, apiClient } from '@/app/api/client'

export type ChatRole = 'user' | 'assistant' | 'system'

export interface ChatMessageDto {
  id: number
  role: ChatRole
  content: string
  ref_type: string | null
  ref_id: number | null
  meta: Record<string, unknown> | null
  created_at: string
}

export interface ChatListDto {
  messages: ChatMessageDto[]
}

export interface CreateTaskDto {
  task_id: number
}

export interface CreateTaskCommand {
  content?: string
  seed_urls?: string[]
  idempotency_key?: string
}

export interface UnderstandDto {
  task_id: number
  status: 'SUCCEEDED' | 'ALREADY_SUCCEEDED' | 'IN_PROGRESS'
  message: ChatMessageDto | null
  result: Record<string, unknown> | null
  spec_draft: Record<string, unknown> | null
  attempt_id: number | null
  trigger_source: string
}

/** Goal Understanding 触发来源：自动触发不产生新 attempt（服务器幂等），
 * 只有用户显式「重新理解」才允许新的模型 attempt / 新费用。 */
export type UnderstandTriggerSource =
  'AUTO_INITIAL' | 'USER_SEND' | 'USER_REUNDERSTAND' | 'RECOVERY'

export interface ConfirmSpecDto {
  task_id: number
  spec_version: number
  state: string
}

export interface SpecDraftResponse {
  task_id: number
  payload: Record<string, unknown> | null
}

export function createTaskDraft(body: CreateTaskCommand): Promise<CreateTaskDto> {
  return apiClient.post<CreateTaskDto>('/tasks', body)
}

export function getChat(taskId: string | number): Promise<ChatListDto> {
  return apiClient.get<ChatListDto>(`/tasks/${taskId}/chat`)
}

export function sendMessage(
  taskId: string | number,
  content: string,
  idempotencyKey?: string,
): Promise<{ message: ChatMessageDto }> {
  return apiClient.post(`/tasks/${taskId}/messages`, {
    content,
    idempotency_key: idempotencyKey,
  })
}

export function runUnderstanding(
  taskId: string | number,
  triggerSource: UnderstandTriggerSource = 'AUTO_INITIAL',
): Promise<UnderstandDto> {
  // 目标理解是同步有界模型调用（backend Provider 有界 ≤45s）；
  // 使用 AI 专用超时（60s 安全网，触发 reconcile 而非业务失败），不继承 CRUD 10s。
  return apiClient.post<UnderstandDto>(
    `/tasks/${taskId}/understand`,
    { trigger_source: triggerSource },
    { timeoutMs: AI_REQUEST_TIMEOUT_MS },
  )
}

export function getSpecDraft(taskId: string | number): Promise<SpecDraftResponse> {
  return apiClient.get<SpecDraftResponse>(`/tasks/${taskId}/spec-draft`)
}

export function updateSpecDraft(
  taskId: string | number,
  payload: Record<string, unknown>,
): Promise<SpecDraftResponse> {
  return apiClient.put<SpecDraftResponse>(`/tasks/${taskId}/spec-draft`, { payload })
}

export function confirmSpec(
  taskId: string | number,
  expectedVersion: number,
  payload?: Record<string, unknown>,
): Promise<ConfirmSpecDto> {
  return apiClient.post<ConfirmSpecDto>(`/tasks/${taskId}/spec-confirm`, {
    expected_version: expectedVersion,
    payload,
  })
}

export function addSeedUrl(taskId: string | number, url: string): Promise<SpecDraftResponse> {
  return apiClient.post<SpecDraftResponse>(`/tasks/${taskId}/seed-urls`, { url })
}
