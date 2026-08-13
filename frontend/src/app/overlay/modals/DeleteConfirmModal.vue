<script setup lang="ts">
// Delete Confirm Modal（D-065/D-067）：
// - soft：删除后进入已删除视图，可恢复（单段确认）。
// - permanent：二次强确认，不可恢复。
import { ref } from 'vue'

import { closeModal } from '@/app/overlay/modal.store'
import { deleteTask, permanentDelete } from '@/features/tasks/commands.api'

type DeleteAction = 'soft' | 'permanent'

interface DeletePayload {
  taskId: string | number
  version: number
  action?: DeleteAction
  onDone?: () => void
}
const props = defineProps<{ payload?: DeletePayload }>()

const action = ref<DeleteAction>(props.payload?.action ?? 'soft')
const step = ref(1) // permanent 两段确认
const running = ref(false)
const error = ref<string | null>(null)

const isPermanent = () => action.value === 'permanent'

async function confirm(): Promise<void> {
  if (!props.payload) return
  if (isPermanent() && step.value === 1) {
    step.value = 2
    return
  }
  running.value = true
  error.value = null
  try {
    const { taskId, version, onDone } = props.payload
    if (isPermanent()) {
      await permanentDelete(taskId, { confirmed: true })
    } else {
      await deleteTask(taskId, { expectedVersion: version })
    }
    onDone?.()
    closeModal()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div class="delete-confirm" data-testid="delete-confirm-modal">
    <!-- permanent 第二段强确认 -->
    <p v-if="isPermanent() && step === 2" class="delete-confirm__danger">
      再次确认：永久删除将删除该任务的全部数据与文件，<strong>不可恢复</strong>。
    </p>
    <p v-else class="delete-confirm__hint">
      {{
        isPermanent()
          ? '永久删除前请确认：任务全部数据与对象文件将被物理清理。'
          : '删除后任务将进入已删除视图，可以恢复。'
      }}
    </p>

    <p v-if="error" class="form-error">{{ error }}</p>

    <div class="delete-confirm__actions">
      <button type="button" class="ghost" @click="closeModal">取消</button>
      <button
        type="button"
        class="danger"
        :disabled="running"
        :data-testid="isPermanent() && step === 1 ? 'permanent-step1' : 'delete-confirm'"
        @click="confirm"
      >
        {{
          running
            ? '处理中…'
            : isPermanent()
              ? step === 1
                ? '永久删除'
                : '确认永久删除'
              : '删除'
        }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.delete-confirm {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.delete-confirm__hint,
.delete-confirm__danger {
  margin: 0;
  font-size: 0.9rem;
}
.delete-confirm__danger {
  color: #c62828;
}
.delete-confirm__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
button.danger {
  padding: 0.5rem 0.9rem;
  border: 1px solid #c62828;
  border-radius: 6px;
  background: #c62828;
  color: #fff;
  cursor: pointer;
  font: inherit;
  font-size: 0.85rem;
}
button.danger:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
