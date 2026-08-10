import { beforeEach, describe, expect, it } from 'vitest'

import { router } from '@/app/router'
import { authStore } from '@/features/auth/useAuth'
import type { UserDto } from '@/features/auth/auth.api'

const mockUser: UserDto = {
  id: 1,
  email: 'alice@example.com',
  display_name: null,
  created_at: '2026-08-10T00:00:00Z',
}

// D-048：全部受保护路由（含 Task 一级/二级页）。
const PROTECTED_ROUTES = [
  '/app',
  '/tasks',
  '/templates',
  '/templates/new',
  '/models',
  '/settings',
  '/tasks/1/chat',
  '/tasks/1/data',
  '/tasks/1/quality',
  '/tasks/1/execution',
  '/tasks/1/evidence/e1',
]

describe.each(PROTECTED_ROUTES)('auth guard for %s', (path) => {
  beforeEach(() => {
    authStore.status.value = 'guest'
    authStore.user.value = null
  })

  it('redirects unauthenticated users to /login', async () => {
    await router.push('/login')
    await router.push(path)
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('allows authenticated users to enter', async () => {
    authStore.status.value = 'authenticated'
    authStore.user.value = mockUser
    await router.push('/app')
    await router.push(path)
    expect(router.currentRoute.value.name).not.toBe('login')
  })
})

describe('guest-only routes', () => {
  beforeEach(() => {
    authStore.status.value = 'authenticated'
    authStore.user.value = mockUser
  })

  it('redirects authenticated users away from /login and /register', async () => {
    await router.push('/app')
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('app')
    await router.push('/register')
    expect(router.currentRoute.value.name).toBe('app')
  })
})
