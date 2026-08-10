import { apiClient } from '@/app/api/client'

export interface TemplateVariableSpec {
  name: string
  label: string
  type?: string
  required?: boolean
  default?: string | null
}

export interface TemplateDto {
  template_id: string
  version: number
  name: string
  task_type: string
  goal_template: string
  variables: TemplateVariableSpec[]
  field_schema: Record<string, unknown>[]
  completion_conditions: Record<string, unknown>[]
  advanced_settings: Record<string, unknown>
  field_expansion: Record<string, unknown>
  is_favorite: boolean
  created_at: string
}

export interface TemplateListDto {
  templates: TemplateDto[]
}

export interface TemplateSpecBody {
  name: string
  task_type: string
  goal_template: string
  variables: TemplateVariableSpec[]
  field_schema: unknown[]
  completion_conditions: unknown[]
  advanced_settings: Record<string, unknown>
  field_expansion: Record<string, unknown>
}

export function listTemplates(): Promise<TemplateListDto> {
  return apiClient.get<TemplateListDto>('/templates')
}

export function getTemplate(templateId: string): Promise<TemplateDto> {
  return apiClient.get<TemplateDto>(`/templates/${templateId}`)
}

export function createTemplate(body: TemplateSpecBody): Promise<TemplateDto> {
  return apiClient.post<TemplateDto>('/templates', body)
}

export function updateTemplate(templateId: string, body: TemplateSpecBody): Promise<TemplateDto> {
  return apiClient.patch<TemplateDto>(`/templates/${templateId}`, body)
}

export function deleteTemplate(templateId: string): Promise<void> {
  return apiClient.delete<void>(`/templates/${templateId}`)
}

export function duplicateTemplate(templateId: string): Promise<TemplateDto> {
  return apiClient.post<TemplateDto>(`/templates/${templateId}/duplicate`)
}

export function setTemplateFavorite(templateId: string, favorite: boolean): Promise<TemplateDto> {
  return apiClient.post<TemplateDto>(`/templates/${templateId}/favorite`, { favorite })
}

export function useTemplate(
  templateId: string,
  variables: Record<string, string>,
): Promise<{ task_id: number }> {
  return apiClient.post<{ task_id: number }>(`/templates/${templateId}/use`, { variables })
}

export function createTemplateFromTask(taskId: string | number): Promise<TemplateDto> {
  return apiClient.post<TemplateDto>(`/tasks/${taskId}/template`)
}
