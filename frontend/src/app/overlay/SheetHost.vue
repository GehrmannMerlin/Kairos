<script setup lang="ts">
import { computed, type Component } from 'vue'

import { closeModal, modalState, type ModalType } from '@/app/overlay/modal.store'
import CollectionSpecEditorSheet from '@/app/overlay/modals/CollectionSpecEditorSheet.vue'
import TemplateVariablesSheet from '@/app/overlay/modals/TemplateVariablesSheet.vue'
import { useEscapeClose } from '@/app/overlay/useEscapeClose'

const SHEET_COMPONENTS: Partial<Record<ModalType, Component>> = {
  COLLECTION_SPEC_EDITOR: CollectionSpecEditorSheet,
  TEMPLATE_VARIABLES: TemplateVariablesSheet,
}

const TITLES: Partial<Record<ModalType, string>> = {
  COLLECTION_SPEC_EDITOR: '采集方案',
  TEMPLATE_VARIABLES: '模板变量',
}

const activeSheet = computed(() => {
  if (!modalState.value.open || !modalState.value.type) return null
  const type = modalState.value.type
  const component = SHEET_COMPONENTS[type]
  if (!component) return null
  return { component, payload: modalState.value.payload, title: TITLES[type] ?? '' }
})

useEscapeClose(closeModal)
</script>

<template>
  <Teleport to="body">
    <div v-if="activeSheet" class="sheet-overlay" role="dialog" aria-modal="true">
      <div class="sheet-backdrop" @click="closeModal" />
      <div class="sheet-panel">
        <header class="sheet-panel__head">
          <h2 class="sheet-panel__title">{{ activeSheet.title }}</h2>
          <button type="button" class="sheet-panel__close" aria-label="关闭" @click="closeModal">
            ×
          </button>
        </header>
        <div class="sheet-panel__body">
          <component :is="activeSheet.component" :payload="activeSheet.payload" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.sheet-overlay {
  position: fixed;
  inset: 0;
  z-index: 55;
  display: flex;
  align-items: flex-end;
}
.sheet-backdrop {
  position: absolute;
  inset: 0;
  background: rgb(0 0 0 / 0.25);
}
.sheet-panel {
  position: relative;
  width: 100%;
  max-height: 70vh;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
  border-radius: 12px 12px 0 0;
  box-shadow: 0 -8px 24px rgb(0 0 0 / 0.08);
  display: flex;
  flex-direction: column;
}
.sheet-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}
.sheet-panel__title {
  font-size: 1.05rem;
  margin: 0;
}
.sheet-panel__close {
  border: none;
  background: none;
  font-size: 1.2rem;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.sheet-panel__body {
  padding: 1.25rem;
  overflow-y: auto;
}
</style>
