import { beforeEach, describe, expect, it, vi } from 'vitest'

import { router } from '@/app/router'
import { authStore } from '@/features/auth/useAuth'
import type { AuthResponseDto, SessionDto, UserDto } from '@/features/auth/auth.api'

vi.mock('@/features/auth/auth.api', () => ({
  fetchMe: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
  changePassword: vi.fn(),
  listSessions: vi.fn(),
  logoutOthers: vi.fn(),
  revokeSession: vi.fn(),
}))

import * as authApi from '@/features/auth/auth.api'

const mockUser: UserDto = {
  id: 1,
  email: 'alice@example.com',
  display_name: null,
  created_at: '2026-08-10T00:00:00Z',
}

const mockSession: SessionDto = {
  id: 1,
  created_at: '2026-08-10T00:00:00Z',
  expires_at: '2026-08-17T00:00:00Z',
  revoked_at: null,
  is_current: true,
}

function mockAuthResponse(): AuthResponseDto {
  return { user: mockUser, session: mockSession }
}

beforeEach(() => {
  vi.clearAllMocks()
  authStore.status.value = 'guest'
  authStore.user.value = null
})

describe('auth router guard', () => {
  it('redirects an unauthenticated user visiting /app to /login', async () => {
    await router.push('/login') // settle initial navigation
    await router.push('/app')
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('redirects an authenticated user visiting /login to /app', async () => {
    authStore.status.value = 'authenticated'
    authStore.user.value = mockUser
    await router.push('/app')
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('app')
  })
})

describe('auth store flows', () => {
  it('login sets authenticated state', async () => {
    vi.mocked(authApi.login).mockResolvedValue(mockAuthResponse())
    await authStore.login('alice@example.com', 'password123')

    expect(authStore.status.value).toBe('authenticated')
    expect(authStore.user.value?.email).toBe('alice@example.com')
    expect(authApi.login).toHaveBeenCalledWith('alice@example.com', 'password123')
  })

  it('register sets authenticated state', async () => {
    vi.mocked(authApi.register).mockResolvedValue(mockAuthResponse())
    await authStore.register('alice@example.com', 'password123', 'password123')

    expect(authStore.status.value).toBe('authenticated')
    expect(authStore.user.value?.email).toBe('alice@example.com')
  })

  it('logout returns to guest state', async () => {
    authStore.status.value = 'authenticated'
    authStore.user.value = mockUser

    await authStore.logout()

    expect(authStore.status.value).toBe('guest')
    expect(authStore.user.value).toBeNull()
  })
})
