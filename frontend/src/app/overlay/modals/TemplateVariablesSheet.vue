<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { mapApiError } from '@/app/error/apiErrorMapper'
import { closeModal } from '@/app/overlay/modal.store'
import { useTemplate, type TemplateDto } from '@/features/templates/templates.api'

// D-047：使用模板 → 填写变量 → 校验 → resolve → 创建 Task Draft → /tasks/:id/chat。
const props = defineProps<{ payload?: { template: TemplateDto } }>()
const router = useRouter()

const values = reactive<Record<string, string>>({})
const using = ref(false)
const error = ref<string | null>(null)

const requiredMissing = (): string | null => {
  for (const v of props.payload?.template.variables ?? []) {
    const filled = (values[v.name] ?? '').trim()
    if (v.required && !filled) return v.label || v.name
  }
  return null
}

async function start(): Promise<void> {
  const missing = requiredMissing()
  if (missing) {
    error.value = `请填写必填变量「${missing}」`
    return
  }
  using.value = true
  error.value = null
  const template = props.payload?.template
  if (!template) return
  try {
    const result = await useTemplate(template.template_id, { ...values })
    closeModal()
    void router.push(`/tasks/${result.task_id}/chat`)
  } catch (err) {
    error.value = mapApiError(err).message
  } finally {
    using.value = false
  }
}
</script>

<template>
  <div class="template-vars">
    <p class="muted">填写变量以使用模板「{{ props.payload?.template.name }}」创建任务。</p>
    <p v-if="error" class="template-vars__error">{{ error }}</p>
    <div v-for="v in props.payload?.template.variables ?? []" :key="v.name" class="field">
      <span class="field__label"> {{ v.label || v.name }}{{ v.required ? ' *' : '' }} </span>
      <input v-model="values[v.name]" class="input" type="text" :placeholder="v.default ?? ''" />
    </div>
    <div class="template-vars__actions">
      <button type="button" class="ghost" @click="closeModal">取消</button>
      <button type="button" :disabled="using" @click="start">
        {{ using ? '创建中…' : '使用模板创建任务' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.template-vars {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.template-vars__error {
  color: #c62828;
  font-size: 0.85rem;
  margin: 0;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.field__label {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.input {
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
}
.template-vars__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
button {
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
button.ghost {
  background: transparent;
  color: var(--color-text);
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.muted {
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}
</style>
