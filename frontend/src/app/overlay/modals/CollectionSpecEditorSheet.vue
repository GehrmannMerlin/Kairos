<script setup lang="ts">
import { reactive, ref } from 'vue'

import { mapApiError } from '@/app/error/apiErrorMapper'
import { closeModal } from '@/app/overlay/modal.store'
import { confirmSpec, updateSpecDraft } from '@/features/tasks/chat.api'
import {
  emptySpecDraft,
  FIELD_TYPES,
  TASK_TYPE_LABELS,
  type SpecDraftPayload,
} from '@/features/tasks/spec.types'

// D-035 完整编辑器 Sheet：目标 / 字段 / 自动扩展 / 范围 / 完成条件 / 高级运行设置
// （默认折叠）。「保存」= 更新 Draft（不等于确认）；「确认并执行」→ confirm_spec。
export interface SpecEditorPayload {
  taskId: string
  expectedVersion: number
  payload: SpecDraftPayload | null
  onChanged?: () => void
}

const props = defineProps<{ payload?: SpecEditorPayload }>()

function clone(p: SpecDraftPayload | null | undefined): SpecDraftPayload {
  const base = p ?? emptySpecDraft()
  return {
    ...base,
    fields: base.fields.map((f) => ({ ...f })),
    source_scope: {
      ...base.source_scope,
      seed_urls: [...base.source_scope.seed_urls],
      source_hints: [...base.source_scope.source_hints],
    },
    completion_conditions: base.completion_conditions.map((c) => ({ ...c })),
    advanced_settings: { ...base.advanced_settings },
  }
}

const form = reactive<SpecDraftPayload>(clone(props.payload?.payload))
const saving = ref(false)
const confirming = ref(false)
const error = ref<string | null>(null)

function addField(): void {
  form.fields.push({ name: '', type: 'text', required: false })
}
function removeField(index: number): void {
  form.fields.splice(index, 1)
}
function addCondition(): void {
  form.completion_conditions.push({ kind: 'min_records', target: 20 })
}
function removeCondition(index: number): void {
  form.completion_conditions.splice(index, 1)
}
function addUrl(): void {
  form.source_scope.seed_urls.push('')
}
function removeUrl(index: number): void {
  form.source_scope.seed_urls.splice(index, 1)
}

async function saveDraft(): Promise<void> {
  saving.value = true
  error.value = null
  try {
    await updateSpecDraft(props.payload?.taskId ?? '', { ...form })
    closeModal()
    props.payload?.onChanged?.()
  } catch (err) {
    error.value = mapApiError(err).message
  } finally {
    saving.value = false
  }
}

async function confirmAndFreeze(): Promise<void> {
  confirming.value = true
  error.value = null
  try {
    await confirmSpec(props.payload?.taskId ?? '', props.payload?.expectedVersion ?? 0, {
      ...form,
    })
    closeModal()
    props.payload?.onChanged?.()
  } catch (err) {
    error.value = mapApiError(err).message
  } finally {
    confirming.value = false
  }
}
</script>

<template>
  <div class="spec-editor">
    <p v-if="error" class="spec-editor__error">{{ error }}</p>

    <label class="field">
      <span>目标</span>
      <textarea v-model="form.goal" rows="2" class="input" />
    </label>

    <label class="field">
      <span>任务类型</span>
      <select v-model="form.task_type" class="input">
        <option :value="null">未识别</option>
        <option v-for="(label, t) in TASK_TYPE_LABELS" :key="t" :value="t">{{ label }}</option>
      </select>
    </label>

    <div class="section">
      <div class="section__head">
        <span>字段定义</span>
        <button type="button" class="ghost" @click="addField">+ 添加字段</button>
      </div>
      <div v-for="(field, idx) in form.fields" :key="idx" class="row">
        <input v-model="field.name" class="input row__name" placeholder="字段名" />
        <select v-model="field.type" class="input row__type">
          <option v-for="t in FIELD_TYPES" :key="t" :value="t">{{ t }}</option>
        </select>
        <label class="row__required"><input v-model="field.required" type="checkbox" /> 必填</label>
        <button type="button" class="ghost" @click="removeField(idx)">删除</button>
      </div>
    </div>

    <label class="check"
      ><input v-model="form.auto_expand_fields" type="checkbox" /> 自动扩展可选字段</label
    >

    <div class="section">
      <div class="section__head">
        <span>采集范围（指定网址）</span>
        <button type="button" class="ghost" @click="addUrl">+ 添加网址</button>
      </div>
      <div v-for="idx in form.source_scope.seed_urls.length" :key="idx" class="row">
        <input
          v-model="form.source_scope.seed_urls[idx - 1]"
          class="input row__name"
          placeholder="https://…"
        />
        <button type="button" class="ghost" @click="removeUrl(idx - 1)">删除</button>
      </div>
    </div>

    <div class="section">
      <div class="section__head">
        <span>完成条件</span>
        <button type="button" class="ghost" @click="addCondition">+ 添加</button>
      </div>
      <div v-for="idx in form.completion_conditions.length" :key="idx" class="row">
        <select v-model="form.completion_conditions[idx - 1].kind" class="input row__type">
          <option value="min_records">最低记录数</option>
          <option value="range_covered">范围覆盖</option>
          <option value="saturation">信息饱和</option>
          <option value="limit">运行限制</option>
        </select>
        <input
          v-model="form.completion_conditions[idx - 1].target"
          class="input row__name"
          type="number"
          placeholder="目标值"
        />
        <button type="button" class="ghost" @click="removeCondition(idx - 1)">删除</button>
      </div>
    </div>

    <details class="advanced">
      <summary>高级运行设置</summary>
      <div class="advanced__grid">
        <label class="field">
          <span>最大处理网页数</span>
          <input v-model="form.advanced_settings.max_pages" type="number" class="input" />
        </label>
        <label class="field">
          <span>最长运行时间（分钟）</span>
          <input
            v-model="form.advanced_settings.max_duration_minutes"
            type="number"
            class="input"
          />
        </label>
        <label class="field">
          <span>单 URL 最大重试次数</span>
          <input v-model="form.advanced_settings.max_retries_per_url" type="number" class="input" />
        </label>
      </div>
    </details>

    <div class="spec-editor__actions">
      <button type="button" class="ghost" :disabled="saving || confirming" @click="saveDraft">
        {{ saving ? '保存中…' : '保存草稿' }}
      </button>
      <button type="button" :disabled="saving || confirming" @click="confirmAndFreeze">
        {{ confirming ? '确认中…' : '确认并执行' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.spec-editor {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.spec-editor__error {
  color: #c62828;
  font-size: 0.85rem;
  margin: 0;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
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
.section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.row__name {
  flex: 1;
}
.row__type {
  width: 8rem;
}
.row__required {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  white-space: nowrap;
}
.check {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
}
button.ghost {
  padding: 0.3rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  font-size: 0.8rem;
}
.advanced {
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.5rem;
  font-size: 0.85rem;
}
.advanced__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.spec-editor__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.spec-editor__actions button {
  padding: 0.5rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
.spec-editor__actions button.ghost {
  background: transparent;
  color: var(--color-text);
}
.spec-editor__actions button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
