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
  created_at: string
}

export function generatePlan(
  taskId: string | number,
  cmd: PlanGenerateCommand,
): Promise<PlanGenerateDto> {
  return apiClient.post<PlanGenerateDto>(`/tasks/${taskId}/plan`, cmd)
}

export function getPlanSummary(
  taskId: string | number,
  planVersion: number,
): Promise<PlanSummaryDto> {
  return apiClient.get<PlanSummaryDto>(`/tasks/${taskId}/plans/${planVersion}`)
}
