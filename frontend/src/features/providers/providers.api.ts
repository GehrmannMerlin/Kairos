import { apiClient } from '@/app/api/client'

export interface ProviderDefinitionDto {
  provider_type: string
  display_name: string
  requires_api_key: boolean
  requires_model_name: boolean
  requires_base_url: boolean
  default_base_url: string | null
  protocol_family: string
  base_url_mode: 'managed' | 'required' | 'local_required'
}

export interface ModelConfigDto {
  config_id: string
  version: number
  name: string
  provider_type: string
  model_name: string
  base_url: string | null
  credential_configured: boolean
  is_default: boolean
  connection_status: string
  last_tested_at: string | null
  created_at: string
}

export interface SearchConfigDto {
  config_id: string
  version: number
  name: string
  provider_type: string
  base_url: string | null
  credential_configured: boolean
  connection_status: string
  last_tested_at: string | null
  created_at: string
}

export interface ProviderTestResultDto {
  status: string
  error_code: string | null
  message: string | null
  latency_ms: number | null
}

export interface ModelProbeResultDto {
  status: string | null
  detection_confidence: 'HIGH' | 'AMBIGUOUS' | 'NONE'
  detected_provider: string | null
  candidates: string[]
  resolved_base_url: string | null
  latency_ms: number | null
  error_code: string | null
  message: string | null
  probe_method: string | null
}

export type DrawerMode = 'create' | 'edit' | 'replaceKey'

/** Structural subset shared by ModelConfigDto / SearchConfigDto for the drawers. */
export interface DrawerConfigRef {
  config_id: string
  name: string
  provider_type: string
  model_name?: string
  base_url: string | null
  is_default?: boolean
  credential_configured?: boolean
}

export interface DefinitionsDto {
  models: ProviderDefinitionDto[]
  searches: ProviderDefinitionDto[]
}

export interface ModelConfigListDto {
  configs: ModelConfigDto[]
  definitions: ProviderDefinitionDto[]
}

export interface SearchConfigListDto {
  configs: SearchConfigDto[]
  definitions: ProviderDefinitionDto[]
}

export function fetchDefinitions(): Promise<DefinitionsDto> {
  return apiClient.get<DefinitionsDto>('/providers/definitions')
}

export function listModelConfigs(): Promise<ModelConfigListDto> {
  return apiClient.get<ModelConfigListDto>('/providers/models')
}

export function createModelConfig(body: Record<string, unknown>): Promise<ModelConfigDto> {
  return apiClient.post<ModelConfigDto>('/providers/models', body)
}

export function updateModelConfig(
  configId: string,
  body: Record<string, unknown>,
): Promise<ModelConfigDto> {
  return apiClient.patch<ModelConfigDto>(`/providers/models/${configId}`, body)
}

export function replaceModelKey(configId: string, apiKey: string): Promise<ModelConfigDto> {
  return apiClient.post<ModelConfigDto>(`/providers/models/${configId}/key`, { api_key: apiKey })
}

export function testModelConnection(configId: string): Promise<ProviderTestResultDto> {
  return apiClient.post<ProviderTestResultDto>(`/providers/models/${configId}/test`)
}

export function probeModel(body: {
  api_key?: string
  provider_type?: string
  base_url?: string
  model_name?: string
}): Promise<ModelProbeResultDto> {
  return apiClient.post<ModelProbeResultDto>('/providers/models/probe', body)
}

export function setModelDefault(configId: string): Promise<ModelConfigDto> {
  return apiClient.post<ModelConfigDto>(`/providers/models/${configId}/default`)
}

export function deleteModelConfig(configId: string): Promise<void> {
  return apiClient.delete<void>(`/providers/models/${configId}`)
}

export function listSearchConfigs(): Promise<SearchConfigListDto> {
  return apiClient.get<SearchConfigListDto>('/providers/searches')
}

export function createSearchConfig(body: Record<string, unknown>): Promise<SearchConfigDto> {
  return apiClient.post<SearchConfigDto>('/providers/searches', body)
}

export function updateSearchConfig(
  configId: string,
  body: Record<string, unknown>,
): Promise<SearchConfigDto> {
  return apiClient.patch<SearchConfigDto>(`/providers/searches/${configId}`, body)
}

export function replaceSearchKey(configId: string, apiKey: string): Promise<SearchConfigDto> {
  return apiClient.post<SearchConfigDto>(`/providers/searches/${configId}/key`, { api_key: apiKey })
}

export function testSearchConnection(configId: string): Promise<ProviderTestResultDto> {
  return apiClient.post<ProviderTestResultDto>(`/providers/searches/${configId}/test`)
}

export function deleteSearchConfig(configId: string): Promise<void> {
  return apiClient.delete<void>(`/providers/searches/${configId}`)
}
