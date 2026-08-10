<script setup lang="ts">
import { computed, type Component } from 'vue'

import { closeModal, modalState, type ModalType } from '@/app/overlay/modal.store'
import DeleteConfirmModal from '@/app/overlay/modals/DeleteConfirmModal.vue'
import ExportModal from '@/app/overlay/modals/ExportModal.vue'
import ModelRequiredModal from '@/app/overlay/modals/ModelRequiredModal.vue'
import { useEscapeClose } from '@/app/overlay/useEscapeClose'

const MODAL_COMPONENTS: Partial<Record<ModalType, Component>> = {
  EXPORT: ExportModal,
  DELETE_CONFIRM: DeleteConfirmModal,
  MODEL_REQUIRED: ModelRequiredModal,
}

const TITLES: Partial<Record<ModalType, string>> = {
  EXPORT: '导出',
  DELETE_CONFIRM: '删除确认',
  MODEL_REQUIRED: '需要配置模型',
}

const activeModal = computed(() => {
  if (!modalState.value.open || !modalState.value.type) return null
  const type = modalState.value.type
  const component = MODAL_COMPONENTS[type]
  if (!component) return null
  return { component, payload: modalState.value.payload, title: TITLES[type] ?? '' }
})

useEscapeClose(closeModal)
</script>

<template>
  <Teleport to="body">
    <div v-if="activeModal" class="modal-overlay" role="dialog" aria-modal="true">
      <div class="modal-backdrop" @click="closeModal" />
      <div class="modal-panel">
        <header class="modal-panel__head">
          <h2 class="modal-panel__title">{{ activeModal.title }}</h2>
          <button type="button" class="modal-panel__close" aria-label="关闭" @click="closeModal">
            ×
          </button>
        </header>
        <div class="modal-panel__body">
          <component :is="activeModal.component" :payload="activeModal.payload" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}
.modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgb(0 0 0 / 0.3);
}
.modal-panel {
  position: relative;
  width: min(480px, 92vw);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  box-shadow: 0 12px 32px rgb(0 0 0 / 0.12);
}
.modal-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}
.modal-panel__title {
  font-size: 1.05rem;
  margin: 0;
}
.modal-panel__close {
  border: none;
  background: none;
  font-size: 1.2rem;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.modal-panel__body {
  padding: 1.25rem;
}
</style>
