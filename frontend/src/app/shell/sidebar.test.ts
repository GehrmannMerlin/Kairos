import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import { router } from '@/app/router'
import AppShell from '@/app/shell/AppShell.vue'
import { authStore } from '@/features/auth/useAuth'
import type { UserDto } from '@/features/auth/auth.api'

const mockUser: UserDto = {
  id: 1,
  email: 'alice@example.com',
  display_name: null,
  created_at: '2026-08-10T00:00:00Z',
}

beforeEach(() => {
  localStorage.clear()
  authStore.status.value = 'authenticated'
  authStore.user.value = mockUser
})

describe('AppShell sidebar collapse', () => {
  it('keeps the route and current user intact', async () => {
    await router.push('/app')
    await router.isReady()

    const wrapper = mount(AppShell, { global: { plugins: [router] } })
    const btn = wrapper.find('.shell__collapse-btn')
    expect(btn.exists()).toBe(true)

    await btn.trigger('click')
    expect(router.currentRoute.value.name).toBe('app')
    expect(authStore.user.value?.email).toBe('alice@example.com')
    expect(authStore.status.value).toBe('authenticated')
    expect(localStorage.getItem('kairos.sidebarCollapsed')).toBe('1')

    await btn.trigger('click')
    expect(localStorage.getItem('kairos.sidebarCollapsed')).toBe('0')
  })
})
