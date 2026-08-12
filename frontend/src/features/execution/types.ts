/** M-14 Execution 工作区类型（对应后端 app/execution/contracts.py）。 */

export type StageKey = 'goal_plan' | 'source_discovery' | 'fetch' | 'extraction' | 'validation'
export type StageState = 'not_started' | 'in_progress' | 'completed' | 'partial' | 'failed'
export type TimelineCategory =
  | 'error'
  | 'retry'
  | 'tool_upgrade'
  | 'plan_change'
  | 'model_call'
  | 'pause_resume'

export interface RunSummary {
  run_id: number
  state: string
  started_at: string | null
  finished_at: string | null
  plan_version: number
  spec_version: number
}

export interface StageSummary {
  key: StageKey
  label: string
  state: StageState
  event_count: number
  url_processed: number
  record_count: number
  error_count: number
}

export interface PlanBrief {
  plan_version: number
  node_count: number
  validation_status: string
}

export interface ExecutionView {
  task_id: number
  run: RunSummary | null
  stages: StageSummary[]
  urls: Record<string, number>
  records: Record<string, number>
  plan: PlanBrief | null
}

export interface TimelineEvent {
  event_id: number
  timestamp: string
  categories: TimelineCategory[]
  stage: string
  summary: string
  status: string | null
  error_code: string | null
  run_id: number | null
  node_run_id: number | null
  node_id: string | null
  retry_count: number
  tool: string | null
  model: string | null
  duration_ms: number | null
  tokens_in: number | null
  tokens_out: number | null
  evidence_refs: number[]
  trace_ref: string | null
}

export interface TimelinePage {
  task_id: number
  items: TimelineEvent[]
  next_cursor: number | null
  has_more: boolean
}

export interface DagNodeExecution {
  event_count: number
  last_status: string | null
  last_error: string | null
  attempt_count: number
  tool: string | null
  model: string | null
  duration_ms: number | null
  tokens_in: number | null
  tokens_out: number | null
  url_fetched_count: number
  record_count: number
}

export interface DagNode {
  node_id: string
  node_type: string
  definition_version: string
  resource_class: string | null
  depends_on: string[]
  optional: boolean
  fail_policy: string
  stage: string
  parameters_summary: Record<string, unknown>
  execution: DagNodeExecution
}

export interface DagEdge {
  from_node_id: string
  to_node_id: string
}

export interface DagView {
  task_id: number
  plan_version: number
  spec_version: number
  validation_status: string
  stage_status: Record<string, string>
  nodes: DagNode[]
  edges: DagEdge[]
}

export interface NodeDetailDto {
  node_id: string
  node_type: string
  definition_version: string
  resource_class: string | null
  depends_on: string[]
  optional: boolean
  fail_policy: string
  plan_version: number
  stage: string
  run: RunSummary | null
  parameters_summary: Record<string, unknown>
  execution: DagNodeExecution
}
