import { AI_REQUEST_TIMEOUT_MS, apiClient } from '@/app/api/client'

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
  // Plan 生成是同步模型调用（生成 + 有界 repair），不是长执行链；HTTP 提交受该
  // 模型调用时长约束，使用 AI 专用超时。
  return apiClient.post<PlanGenerateDto>(`/tasks/${taskId}/plan`, cmd, {
    timeoutMs: AI_REQUEST_TIMEOUT_MS,
  })
}

export function getPlanSummary(
  taskId: string | number,
  planVersion: number,
): Promise<PlanSummaryDto> {
  return apiClient.get<PlanSummaryDto>(`/tasks/${taskId}/plans/${planVersion}`)
}
