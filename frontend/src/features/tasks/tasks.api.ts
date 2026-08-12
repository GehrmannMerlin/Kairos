import { apiClient } from '@/app/api/client'

/** owner-safe Task Shell Query DTO（对应后端 GET /api/tasks）。 */
export interface TaskShellDto {
  task_id: number
  title: string
  state: string
  version: number
  task_type: string | null
  current_spec_version: number | null
  current_plan_version: number | null
  template_id: string | null
  template_version: number | null
  allowed_actions: string[]
  created_at: string
  updated_at: string
}

export interface TaskShellListDto {
  tasks: TaskShellDto[]
}

export interface ListTasksOptions {
  view?: string
}

export function listTasks(options: ListTasksOptions = {}): Promise<TaskShellListDto> {
  const qs = options.view ? `?view=${options.view}` : ''
  return apiClient.get<TaskShellListDto>(`/tasks${qs}`)
}

export function getTask(taskId: string | number): Promise<TaskShellDto> {
  return apiClient.get<TaskShellDto>(`/tasks/${taskId}`)
}
