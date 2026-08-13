<script setup lang="ts">
import { computed, type Component } from 'vue'

import { closeDrawer, drawerState, type DrawerType } from '@/app/overlay/drawer.store'
import { useEscapeClose } from '@/app/overlay/useEscapeClose'
import ApprovalDrawer from '@/app/overlay/drawers/ApprovalDrawer.vue'
import CredentialDrawer from '@/app/overlay/drawers/CredentialDrawer.vue'
import EvidenceQuickDrawer from '@/app/overlay/drawers/EvidenceQuickDrawer.vue'
import NodeDetailDrawer from '@/app/overlay/drawers/NodeDetailDrawer.vue'
import ProviderEditDrawer from '@/app/overlay/drawers/ProviderEditDrawer.vue'
import RecordDrawer from '@/app/overlay/drawers/RecordDrawer.vue'
import TaskStatusDrawer from '@/app/overlay/drawers/TaskStatusDrawer.vue'

const drawerComponents: Record<DrawerType, Component> = {
  TASK_STATUS: TaskStatusDrawer,
  APPROVAL: ApprovalDrawer,
  CREDENTIAL: CredentialDrawer,
  RECORD: RecordDrawer,
  EVIDENCE_QUICK: EvidenceQuickDrawer,
  NODE_DETAIL: NodeDetailDrawer,
  PROVIDER_EDIT: ProviderEditDrawer,
}

const TITLES: Record<DrawerType, string> = {
  TASK_STATUS: '任务状态',
  APPROVAL: '审批',
  CREDENTIAL: '网站凭据',
  RECORD: '记录详情',
  EVIDENCE_QUICK: '证据',
  NODE_DETAIL: '节点详情',
  PROVIDER_EDIT: '模型 / 搜索配置',
}

const activeDrawer = computed(() => {
  if (!drawerState.value.open || !drawerState.value.type) return null
  const type = drawerState.value.type
  return {
    component: drawerComponents[type],
    payload: drawerState.value.payload,
    title: TITLES[type],
  }
})

useEscapeClose(closeDrawer)
</script>

<template>
  <Teleport to="body">
    <div v-if="activeDrawer" class="drawer-overlay" role="dialog" aria-modal="true">
      <div class="drawer-backdrop" @click="closeDrawer" />
      <aside ref="panelRef" class="drawer-panel">
        <header class="drawer-panel__head">
          <h2 class="drawer-panel__title">{{ activeDrawer.title }}</h2>
          <button type="button" class="drawer-panel__close" aria-label="关闭" @click="closeDrawer">
            ×
          </button>
        </header>
        <div class="drawer-panel__body">
          <component :is="activeDrawer.component" :payload="activeDrawer.payload" />
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
}
.drawer-backdrop {
  position: absolute;
  inset: 0;
  background: rgb(0 0 0 / 0.25);
}
.drawer-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(420px, 92vw);
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: -8px 0 24px rgb(0 0 0 / 0.08);
  display: flex;
  flex-direction: column;
}
.drawer-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}
.drawer-panel__title {
  font-size: 1.05rem;
  margin: 0;
}
.drawer-panel__close {
  border: none;
  background: none;
  font-size: 1.2rem;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.drawer-panel__body {
  padding: 1.25rem;
  overflow-y: auto;
}
</style>
