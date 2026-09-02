export type WorkspacePermission = 'NONE' | 'READ_ONLY' | 'READ_WRITE' | 'LOCAL_FULL_ACCESS'
export type TaskStatus = 'DRAFT' | 'RUNNING' | 'COMPLETED' | 'PARTIALLY_COMPLETED' | 'FAILED'
export type TaskRunStatus = 'RUNNING' | 'COMPLETED' | 'PARTIALLY_COMPLETED' | 'FAILED'
export type CollectionFieldType = 'STRING' | 'INTEGER' | 'NUMBER' | 'BOOLEAN' | 'DATE' | 'URL'
export type CollectionSourceStatus = 'PENDING' | 'FETCHED' | 'PROCESSED' | 'FAILED' | 'BLOCKED'
export type RecordStatus = 'PASSED' | 'NEEDS_REVIEW' | 'REJECTED'

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
  spec_version_id: string | null
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

export interface CollectionField {
  name: string
  type: CollectionFieldType
  required: boolean
  description?: string | null
}

export interface CollectionSource {
  source_id: string
  url: string
  canonical_url: string
  origin: string
  status: CollectionSourceStatus
  snapshot_id: string | null
  failure_code: string | null
}

export interface CollectionSpec {
  spec_version_id: string
  owner_id: string
  task_id: string
  version: number
  mode: 'SPECIFIED_SOURCE'
  goal: string
  fields: CollectionField[]
  seed_urls: string[]
  target_count: number | null
  confirmed_at: string
  created_at: string
  sources: CollectionSource[]
}

export interface CollectionProgress {
  total_sources: number
  pending_sources: number
  fetched_sources: number
  processed_sources: number
  failed_sources: number
  blocked_sources: number
  total_records: number
  passed_records: number
  needs_review_records: number
  rejected_records: number
  remaining_sources: number
}

export interface CollectionRecord {
  record_id: string
  snapshot_id: string
  ordinal: number
  fields: Record<string, unknown>
  status: RecordStatus
  validation_issues: string[]
}

export interface FieldEvidence {
  evidence_id: string
  field_name: string
  value: unknown
  source_url: string
  quote: string
  verified: boolean
  confidence: number | null
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
  confirmCollectionSpec: (taskId: string, body: { goal: string; fields: CollectionField[]; seed_urls: string[] }) =>
    request<CollectionSpec>(`/api/tasks/${taskId}/collection/spec`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getCollectionSpec: (taskId: string) => request<CollectionSpec>(`/api/tasks/${taskId}/collection/spec`),
  getCollectionProgress: (taskId: string) =>
    request<CollectionProgress>(`/api/tasks/${taskId}/collection/progress`),
  getRecords: (taskId: string) => request<CollectionRecord[]>(`/api/tasks/${taskId}/records`),
  getRecordEvidence: (taskId: string, recordId: string) =>
    request<FieldEvidence[]>(`/api/tasks/${taskId}/records/${recordId}/evidence`),
}

export function eventSource(taskId: string, runId: string): EventSource {
  return new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/events?run_id=${encodeURIComponent(runId)}`)
}
