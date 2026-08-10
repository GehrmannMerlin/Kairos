import { ref, type Ref } from 'vue'

import * as authApi from '@/features/auth/auth.api'
import type { UserDto } from '@/features/auth/auth.api'

export type AuthStatus = 'loading' | 'authenticated' | 'guest'

export interface AuthStore {
  status: Ref<AuthStatus>
  user: Ref<UserDto | null>
  init: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, confirmPassword: string) => Promise<void>
  logout: () => Promise<void>
}

function createAuthStore(): AuthStore {
  const status = ref<AuthStatus>('loading')
  const user = ref<UserDto | null>(null)

  async function init(): Promise<void> {
    try {
      user.value = await authApi.fetchMe()
      status.value = 'authenticated'
    } catch {
      user.value = null
      status.value = 'guest'
    }
  }

  async function login(email: string, password: string): Promise<void> {
    const response = await authApi.login(email, password)
    user.value = response.user
    status.value = 'authenticated'
  }

  async function register(email: string, password: string, confirmPassword: string): Promise<void> {
    const response = await authApi.register(email, password, confirmPassword)
    user.value = response.user
    status.value = 'authenticated'
  }

  async function logout(): Promise<void> {
    await authApi.logout()
    user.value = null
    status.value = 'guest'
  }

  return { status, user, init, login, register, logout }
}

export const authStore: AuthStore = createAuthStore()
