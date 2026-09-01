export type WorkspacePermission = 'NONE' | 'READ_ONLY' | 'READ_WRITE' | 'LOCAL_FULL_ACCESS'
export type TaskStatus = 'DRAFT' | 'RUNNING' | 'COMPLETED' | 'FAILED'
export type TaskRunStatus = 'RUNNING' | 'COMPLETED' | 'FAILED'

export interface Workspace {
  workspace_id: string
  owner_id: string
  display_name: string
  root_path: string
  permission_mode: WorkspacePermission
  enabled: boolean
}

export interface Task {
  task_id: string
  owner_id: string
  workspace_id: string | null
  status: TaskStatus
  latest_run?: Run | null
  final_answer?: string | null
  error_message?: string | null
}

export interface Run {
  task_run_id: string
  workflow_id: string
  status: TaskRunStatus
}

export interface AgentEvent {
  event_id: number
  event_type: string
  task_id: string
  task_run_id: string
  tool_name: string | null
  summary: string | null
  payload: Record<string, unknown>
  occurred_at: string | null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-Kairos-User-Id': 'local-user',
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `Request failed: ${response.status}`)
  }
  return (await response.json()) as T
}

export const api = {
  listWorkspaces: () => request<Workspace[]>('/api/workspaces'),
  createWorkspace: (body: Pick<Workspace, 'display_name' | 'root_path' | 'permission_mode'>) =>
    request<Workspace>('/api/workspaces', { method: 'POST', body: JSON.stringify(body) }),
  createTask: () => request<Task>('/api/tasks', { method: 'POST' }),
  bindWorkspace: (taskId: string, workspaceId: string) =>
    request<Task>(`/api/tasks/${taskId}/workspace?workspace_id=${encodeURIComponent(workspaceId)}`, {
      method: 'POST',
    }),
  startRun: (taskId: string, prompt: string) =>
    request<Run>(`/api/tasks/${taskId}/runs`, {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    }),
  getTask: (taskId: string) => request<Task>(`/api/tasks/${taskId}`),
}

export function eventSource(taskId: string, runId: string): EventSource {
  return new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/events?run_id=${encodeURIComponent(runId)}`)
}

