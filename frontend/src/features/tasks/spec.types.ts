// CollectionSpec typed schema (mirrors backend app.domain.spec). Single canonical
// TaskType vocabulary; no money/budget fields (D-036); concurrency stays out (D-071).
export type TaskType = 'EXPLORATORY' | 'SPECIFIED_SOURCE' | 'HYBRID'

export interface FieldSpecDto {
  name: string
  type: string
  required: boolean
  description?: string | null
}

export interface SourceScopeDto {
  mode: TaskType
  seed_urls: string[]
  source_hints: string[]
}

export interface CompletionConditionDto {
  kind: string
  target?: number | null
  threshold?: number | null
  note?: string | null
}

export interface RuntimeLimitsDto {
  max_pages?: number | null
  max_duration_minutes?: number | null
  max_retries_per_url?: number | null
}

export interface SpecDraftPayload {
  schema_version: string
  task_type: TaskType | null
  task_name: string | null
  goal: string
  fields: FieldSpecDto[]
  auto_expand_fields: boolean
  source_scope: SourceScopeDto
  completion_conditions: CompletionConditionDto[]
  advanced_settings: RuntimeLimitsDto
  field_expansion: Record<string, unknown>
}

export const TASK_TYPE_LABELS: Record<TaskType, string> = {
  EXPLORATORY: '探索式搜集',
  SPECIFIED_SOURCE: '指定来源抽取',
  HYBRID: '混合式',
}

export const FIELD_TYPES = [
  'text',
  'number',
  'url',
  'email',
  'phone',
  'date',
  'boolean',
  'other',
] as const

/** Backend returns the draft payload as a plain dict; normalize for the typed UI. */
export function asSpecDraftPayload(value: unknown): SpecDraftPayload | null {
  if (!value || typeof value !== 'object') return null
  return value as SpecDraftPayload
}

export function emptySpecDraft(): SpecDraftPayload {
  return {
    schema_version: 'm06.1',
    task_type: null,
    task_name: null,
    goal: '',
    fields: [],
    auto_expand_fields: false,
    source_scope: { mode: 'EXPLORATORY', seed_urls: [], source_hints: [] },
    completion_conditions: [],
    advanced_settings: {},
    field_expansion: {},
  }
}
