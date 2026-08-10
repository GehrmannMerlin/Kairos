import { apiClient } from '@/app/api/client'

export interface UserDto {
  id: number
  email: string
  display_name: string | null
  created_at: string
}

export interface SessionDto {
  id: number
  created_at: string
  expires_at: string
  revoked_at: string | null
  is_current: boolean
}

export interface AuthResponseDto {
  user: UserDto
  session: SessionDto
}

export interface SessionsResponseDto {
  sessions: SessionDto[]
}

export function register(
  email: string,
  password: string,
  confirmPassword: string,
): Promise<AuthResponseDto> {
  return apiClient.post<AuthResponseDto>('/auth/register', {
    email,
    password,
    confirm_password: confirmPassword,
  })
}

export function login(email: string, password: string): Promise<AuthResponseDto> {
  return apiClient.post<AuthResponseDto>('/auth/login', { email, password })
}

export function logout(): Promise<void> {
  return apiClient.post<void>('/auth/logout')
}

export function fetchMe(): Promise<UserDto> {
  return apiClient.get<UserDto>('/auth/me')
}

export function changePassword(
  currentPassword: string,
  newPassword: string,
  confirmPassword: string,
): Promise<AuthResponseDto> {
  return apiClient.post<AuthResponseDto>('/auth/password', {
    current_password: currentPassword,
    new_password: newPassword,
    confirm_password: confirmPassword,
  })
}

export function listSessions(): Promise<SessionsResponseDto> {
  return apiClient.get<SessionsResponseDto>('/auth/sessions')
}

export function logoutOthers(): Promise<void> {
  return apiClient.post<void>('/auth/sessions/logout-others')
}

export function revokeSession(sessionId: number): Promise<void> {
  return apiClient.delete<void>(`/auth/sessions/${sessionId}`)
}
