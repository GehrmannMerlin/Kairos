<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import DrawerHost from '@/app/overlay/DrawerHost.vue'
import ModalHost from '@/app/overlay/ModalHost.vue'
import SheetHost from '@/app/overlay/SheetHost.vue'
import { appNotices, dismissNotice } from '@/app/error/useAppNotice'
import { authStore } from '@/features/auth/useAuth'

const SIDEBAR_KEY = 'kairos.sidebarCollapsed'

const router = useRouter()
const route = useRoute()

/** Collapse is a pure UI layout preference; it never clears stores, changes the
 * route, reloads data or affects the session. Persisted to localStorage only. */
const collapsed = ref(localStorage.getItem(SIDEBAR_KEY) === '1')
const userMenuOpen = ref(false)

const displayName = computed(() => authStore.user.value?.display_name ?? null)
const email = computed(() => authStore.user.value?.email ?? '')
const pageTitle = computed(() => (typeof route.meta.title === 'string' ? route.meta.title : ''))

const navItems = [
  { to: '/app', label: '工作台' },
  { to: '/app', label: '＋ 新任务' },
  { to: '/tasks', label: '我的任务' },
  { to: '/templates', label: '模板' },
  { to: '/models', label: '模型配置' },
  { to: '/settings', label: '设置' },
]

function toggleSidebar(): void {
  collapsed.value = !collapsed.value
  localStorage.setItem(SIDEBAR_KEY, collapsed.value ? '1' : '0')
}

async function onLogout(): Promise<void> {
  userMenuOpen.value = false
  await authStore.logout()
  await router.push('/login')
}
</script>

<template>
  <div class="shell" :class="{ 'shell--collapsed': collapsed }">
    <aside class="shell__sidebar">
      <div class="shell__brand-row">
        <span class="shell__brand">Kairos</span>
        <button
          type="button"
          class="shell__collapse-btn"
          :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'"
          @click="toggleSidebar"
        >
          {{ collapsed ? '»' : '«' }}
        </button>
      </div>
      <nav class="shell__nav" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="`${item.to}-${item.label}`"
          :to="item.to"
          :title="item.label"
          class="shell__nav-item"
        >
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <div class="shell__body">
      <header class="shell__topbar">
        <h1 class="shell__title">{{ pageTitle }}</h1>
        <div class="shell__user">
          <button type="button" class="shell__user-trigger" @click="userMenuOpen = !userMenuOpen">
            {{ displayName ?? email }}
          </button>
          <div v-if="userMenuOpen" class="shell__menu-backdrop" @click="userMenuOpen = false" />
          <ul v-if="userMenuOpen" class="shell__menu">
            <li>
              <RouterLink to="/settings" @click="userMenuOpen = false">设置</RouterLink>
            </li>
            <li>
              <button type="button" @click="onLogout">退出登录</button>
            </li>
          </ul>
        </div>
      </header>
      <main class="shell__main">
        <slot />
      </main>

      <div v-if="appNotices.length" class="shell__notices" aria-live="polite">
        <div
          v-for="notice in appNotices"
          :key="notice.id"
          class="shell__notice"
          :class="`shell__notice--${notice.kind}`"
        >
          <span>{{ notice.message }}</span>
          <button type="button" aria-label="关闭" @click="dismissNotice(notice.id)">×</button>
        </div>
      </div>

      <DrawerHost />
      <ModalHost />
      <SheetHost />
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}
.shell__sidebar {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: 220px;
  border-right: 1px solid var(--color-border);
  transition: width 0.18s ease;
}
.shell--collapsed .shell__sidebar {
  width: 56px;
}
.shell__brand-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
}
.shell--collapsed .shell__brand-row {
  justify-content: center;
  padding-inline: 0.5rem;
}
.shell__brand {
  font-weight: 700;
  font-size: 1.05rem;
}
.shell--collapsed .shell__brand {
  display: none;
}
.shell__collapse-btn {
  border: 1px solid var(--color-border);
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  padding: 0.1rem 0.4rem;
}
.shell__nav {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.5rem;
}
.shell__nav-item {
  padding: 0.5rem 0.75rem;
  color: var(--color-text-secondary);
  text-decoration: none;
  border-radius: 6px;
  white-space: nowrap;
  overflow: hidden;
}
.shell__nav-item:hover {
  background: var(--color-border);
  color: var(--color-text);
}
.shell__nav-item.router-link-active {
  background: var(--color-border);
  color: var(--color-text);
  font-weight: 600;
}
.shell--collapsed .shell__nav-item {
  font-size: 0;
  text-align: center;
}
.shell--collapsed .shell__nav-item::first-letter {
  font-size: 1rem;
}
.shell__body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}
.shell__topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0 1.5rem;
  height: 3.5rem;
  border-bottom: 1px solid var(--color-border);
}
.shell__title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0;
}
.shell__main {
  flex: 1;
  padding: 1.5rem;
  min-width: 0;
}
.shell__user {
  position: relative;
}
.shell__user-trigger {
  padding: 0.4rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.shell__menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 10;
}
.shell__menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 11;
  list-style: none;
  margin: 0;
  padding: 0.25rem;
  min-width: 140px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  box-shadow: 0 4px 16px rgb(0 0 0 / 0.08);
}
.shell__menu a,
.shell__menu button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.75rem;
  border: none;
  background: none;
  color: var(--color-text);
  text-decoration: none;
  cursor: pointer;
  border-radius: 6px;
}
.shell__menu a:hover,
.shell__menu button:hover {
  background: var(--color-border);
}
.shell__notices {
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  z-index: 70;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-width: min(360px, 90vw);
}
.shell__notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  box-shadow: 0 4px 16px rgb(0 0 0 / 0.08);
  font-size: 0.9rem;
}
.shell__notice--error {
  border-color: var(--color-danger);
}
.shell__notice button {
  border: none;
  background: none;
  cursor: pointer;
  color: var(--color-text-secondary);
}
</style>
