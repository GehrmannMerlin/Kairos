import type { LocationQuery, LocationQueryValue } from 'vue-router'

/** Task 二级 Deep Link 统一解析（D-057/D-060/D-062）。页面不再手写 query 强转。 */
export interface TaskDeepLinkQuery {
  approval?: string
  status?: string
  review_type?: string
  source_type?: string
}

function firstString(value: LocationQueryValue | LocationQueryValue[]): string | undefined {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.find((v): v is string => typeof v === 'string')
  return undefined
}

export function parseTaskQuery(query: LocationQuery): TaskDeepLinkQuery {
  return {
    approval: firstString(query.approval),
    status: firstString(query.status),
    review_type: firstString(query.review_type),
    source_type: firstString(query.source_type),
  }
}
