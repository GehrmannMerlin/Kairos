<script setup lang="ts">
import { useRouter } from 'vue-router'

import AppShell from '@/app/shell/AppShell.vue'
import { authStore } from '@/features/auth/useAuth'

const router = useRouter()

async function onLogout(): Promise<void> {
  await authStore.logout()
  await router.push('/login')
}
</script>

<template>
  <AppShell>
    <section class="app-placeholder">
      <h1>工作区（M-02 受保护占位）</h1>
      <p>当前用户：{{ authStore.user.value?.email }}</p>
      <button @click="onLogout">退出登录</button>
    </section>
  </AppShell>
</template>

<style scoped>
.app-placeholder h1 {
  font-size: 1.25rem;
  margin: 0 0 0.5rem;
}
.app-placeholder p {
  color: var(--color-text-secondary);
}
.app-placeholder button {
  margin-top: 1rem;
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
}
</style>
