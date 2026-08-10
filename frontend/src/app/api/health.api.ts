import { apiClient } from '@/app/api/client'

export interface HealthLiveDto {
  status: string
  service: string
}

export interface HealthCheckDto {
  status: string
  error: string | null
}

export interface HealthReadyDto {
  status: string
  checks: Record<string, HealthCheckDto>
}

export function fetchHealthLive(): Promise<HealthLiveDto> {
  return apiClient.get<HealthLiveDto>('/health/live')
}

export function fetchHealthReady(): Promise<HealthReadyDto> {
  return apiClient.get<HealthReadyDto>('/health/ready')
}
