/** M-14 统一 Data 页 Deep Link 生成器（D-062 / D-060）。
 *
 * Quality 指标下钻唯一入口：组件不得手工拼接 URL。字段与 M-13
 * TaskDataView 解析的 query 完全一致（status=review 归一化为 needs_review）。
 */
import type { LocationQueryRaw } from 'vue-router'

export interface DataDrilldown {
  status?: 'passed' | 'review' | 'rejected' | null
  review_type?: string | null
  source_type?: string | null
  extract_method?: string | null
  min_confidence?: number | null
}

export function buildDataLink(
  taskId: string | number,
  drill: DataDrilldown,
): { name: 'task-data'; params: { taskId: string }; query: LocationQueryRaw } {
  const query: LocationQueryRaw = {}
  if (drill.status) query.status = drill.status
  if (drill.review_type) query.review_type = drill.review_type
  if (drill.source_type) query.source_type = drill.source_type
  if (drill.extract_method) query.extract_method = drill.extract_method
  if (drill.min_confidence != null) query.min_confidence = String(drill.min_confidence)
  return { name: 'task-data', params: { taskId: String(taskId) }, query }
}

export function drilldownIsEmpty(drill: DataDrilldown): boolean {
  return !(
    drill.status ||
    drill.review_type ||
    drill.source_type ||
    drill.extract_method ||
    drill.min_confidence != null
  )
}
