import { ref, type Ref } from 'vue'

import * as authApi from '@/features/auth/auth.api'
import type { UserDto } from '@/features/auth/auth.api'

export type AuthStatus = 'loading' | 'authenticated' | 'guest'

/** CurrentUserStore 契约：user / status / loading / error + loadCurrentUser / logout。
 * 路由守卫与全局用户菜单复用同一实例，不重复请求 /me。 */
export interface AuthStore {
  status: Ref<AuthStatus>
  user: Ref<UserDto | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  init: () => Promise<void>
  loadCurrentUser: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, confirmPassword: string) => Promise<void>
  logout: () => Promise<void>
}

function createAuthStore(): AuthStore {
  const status = ref<AuthStatus>('loading')
  const user = ref<UserDto | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function loadCurrentUser(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      user.value = await authApi.fetchMe()
      status.value = 'authenticated'
    } catch {
      user.value = null
      status.value = 'guest'
    } finally {
      loading.value = false
    }
  }

  const init = loadCurrentUser

  async function login(email: string, password: string): Promise<void> {
    error.value = null
    try {
      const response = await authApi.login(email, password)
      user.value = response.user
      status.value = 'authenticated'
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    }
  }

  async function register(email: string, password: string, confirmPassword: string): Promise<void> {
    error.value = null
    try {
      const response = await authApi.register(email, password, confirmPassword)
      user.value = response.user
      status.value = 'authenticated'
    } catch (err) {
      error.value = err instanceof Error ? err.message : String(err)
      throw err
    }
  }

  async function logout(): Promise<void> {
    error.value = null
    await authApi.logout()
    user.value = null
    status.value = 'guest'
  }

  return { status, user, loading, error, init, loadCurrentUser, login, register, logout }
}

export const authStore: AuthStore = createAuthStore()
