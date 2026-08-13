/** M-14 Evidence Viewer 类型（对应后端 app/evidence/contracts.py）。 */

export type EvidenceDisplayMode = 'snapshot' | 'text' | 'raw'

export interface EvidenceFieldEvidence {
  record_id: number
  field_name: string
  value: string | null
  raw_snippet: string | null
  source_locator: string | null
  extract_method: string | null
  extractor_version: string | null
  confidence: number | null
}

export interface EvidenceView {
  evidence_id: number
  task_id: number
  source_url: string
  fetched_at: string | null
  snapshot_version: number
  tool: string
  tool_version: string
  mime_type: string | null
  http_status: number | null
  content_length: number | null
  display_mode: EvidenceDisplayMode
  summary: string | null
  field_evidence: EvidenceFieldEvidence[]
  has_content: boolean
  download_url: string
}

export interface EvidenceContent {
  text: string
  contentType: string
  isImage: boolean
  imageUrl?: string
}

export interface LocateResult {
  found: boolean
  snippet: string
}
