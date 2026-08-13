import { apiClient } from '@/app/api/client'

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
  message: ChatMessageDto
  result: Record<string, unknown>
  spec_draft: Record<string, unknown>
}

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

export function runUnderstanding(taskId: string | number): Promise<UnderstandDto> {
  return apiClient.post<UnderstandDto>(`/tasks/${taskId}/understand`)
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
