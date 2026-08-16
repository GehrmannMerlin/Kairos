import { apiClient } from '@/app/api/client'

/** Plan 生成（D-038：合法低风险 Plan 自动启动，不弹二次确认）。 */
export interface PlanGenerateCommand {
  spec_version: number
  expected_version: number
}

export interface PlanGenerateDto {
  task_id: number
  plan_version: number
  validation_status: string
  node_count: number
  run_id: number | null
  workflow_id: string | null
  run_state: string | null
  start_recoverable: boolean
  validator_issues: ValidatorIssueSummary[]
  preflight_status?: string | null
  preflight_issues?: PreflightIssueSummary[]
}

export interface ValidatorIssueSummary {
  code: string
  node_id?: string | null
  edge_index?: number | null
  field?: string | null
  message?: string | null
}

export interface PreflightIssueSummary {
  code: string
  safe_message: string
  node_id?: string | null
  field?: string | null
  remediation?: string | null
}

/** Plan 摘要（D-025 / D-055：Chat 内简洁摘要，不新增 /plan 页面）。 */
export interface PlanSummaryDto {
  task_id: number
  plan_version: number
  spec_version: number
  validation_status: string
  plan_fingerprint: string
  node_count: number
  node_types: (string | null)[]
  diff_summary: Record<string, unknown> | null
  trigger_reason: string | null
  run_id: number | null
  run_state: string | null
  start_recoverable: boolean
  validator_issues: ValidatorIssueSummary[]
  preflight_status?: string | null
  preflight_issues?: PreflightIssueSummary[]
  created_at: string
}

export function generatePlan(
  taskId: string | number,
  cmd: PlanGenerateCommand,
  signal?: AbortSignal,
): Promise<PlanGenerateDto> {
  // 浏览器不抢先裁决完整 Plan 生命周期；Provider、Plan 生命周期和反代各自有界。
  return apiClient.post<PlanGenerateDto>(`/tasks/${taskId}/plan`, cmd, {
    timeoutMs: null,
    ...(signal ? { signal } : {}),
  })
}

/** 只恢复已持久化 Plan 的 Workflow 启动；绝不隐式重新生成。 */
export function startPlan(
  taskId: string | number,
  planVersion: number,
  signal?: AbortSignal,
): Promise<PlanGenerateDto> {
  return apiClient.post<PlanGenerateDto>(
    `/tasks/${taskId}/plans/${planVersion}/start`,
    undefined,
    signal ? { signal } : undefined,
  )
}

export function getPlanSummary(
  taskId: string | number,
  planVersion: number,
): Promise<PlanSummaryDto> {
  return apiClient.get<PlanSummaryDto>(`/tasks/${taskId}/plans/${planVersion}`)
}
