<script setup lang="ts">
import { openDrawer } from '@/app/overlay/drawer.store'

// Chat 时间线“需要凭据”卡片（D-059 / M-10）。点击打开 Credential Drawer；
// Drawer 保存后走 credential_access Approval（D-017），不在这里假装完成。
defineProps<{ taskId: string | number; domain?: string }>()

function openCredentialDrawer(props: { taskId: string | number; domain?: string }): void {
  openDrawer('CREDENTIAL', { taskId: props.taskId, domain: props.domain })
}
</script>

<template>
  <button type="button" class="credential-card" @click="openCredentialDrawer($props)">
    <span class="credential-card__title">需要凭据</span>
    <span v-if="domain" class="credential-card__domain">{{ domain }}</span>
    <span class="credential-card__action">提供凭据</span>
  </button>
</template>

<style scoped>
.credential-card {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  cursor: pointer;
  font: inherit;
  font-size: 0.85rem;
}
.credential-card__title {
  font-weight: 600;
}
.credential-card__domain {
  color: var(--color-text-secondary);
}
.credential-card__action {
  margin-left: auto;
  color: var(--color-text);
  text-decoration: underline;
}
</style>
