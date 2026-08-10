<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { mapApiError } from '@/app/error/apiErrorMapper'
import {
  createTemplate,
  getTemplate,
  updateTemplate,
  type TemplateSpecBody,
} from '@/features/templates/templates.api'
import { FIELD_TYPES, TASK_TYPE_LABELS, type TaskType } from '@/features/tasks/spec.types'

// D-054 全宽模板编辑页：/templates/new 与 /templates/:templateId/edit。
// 保存创建或更新 Template Version；历史任务不受后续修改影响。
const route = useRoute()
const router = useRouter()
const templateId = computed(() =>
  typeof route.params.templateId === 'string' ? route.params.templateId : null,
)

interface VarRow {
  name: string
  label: string
  required: boolean
}
interface FieldRow {
  name: string
  type: string
  required: boolean
}
interface CondRow {
  kind: string
  target: number | null
}

const form = reactive<{
  name: string
  task_type: TaskType
  goal_template: string
  variables: VarRow[]
  fields: FieldRow[]
  conditions: CondRow[]
  max_pages: number | null
  max_duration_minutes: number | null
  max_retries_per_url: number | null
}>({
  name: '',
  task_type: 'EXPLORATORY',
  goal_template: '',
  variables: [],
  fields: [],
  conditions: [],
  max_pages: null,
  max_duration_minutes: null,
  max_retries_per_url: null,
})

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

function addVar(): void {
  form.variables.push({ name: '', label: '', required: false })
}
function removeVar(i: number): void {
  form.variables.splice(i, 1)
}
function addField(): void {
  form.fields.push({ name: '', type: 'text', required: false })
}
function removeField(i: number): void {
  form.fields.splice(i, 1)
}
function addCondition(): void {
  form.conditions.push({ kind: 'min_records', target: 20 })
}
function removeCondition(i: number): void {
  form.conditions.splice(i, 1)
}

function toBody(): TemplateSpecBody {
  return {
    name: form.name.trim() || '未命名模板',
    task_type: form.task_type,
    goal_template: form.goal_template,
    variables: form.variables.map((v) => ({
      name: v.name.trim(),
      label: v.label.trim(),
      required: v.required,
    })),
    field_schema: form.fields.map((f) => ({
      name: f.name.trim(),
      type: f.type,
      required: f.required,
    })),
    completion_conditions: form.conditions.map((c) => ({ kind: c.kind, target: c.target })),
    advanced_settings: {
      max_pages: form.max_pages,
      max_duration_minutes: form.max_duration_minutes,
      max_retries_per_url: form.max_retries_per_url,
    },
    field_expansion: {},
  }
}

async function loadTemplate(): Promise<void> {
  if (!templateId.value) return
  loading.value = true
  error.value = null
  try {
    const t = await getTemplate(templateId.value)
    form.name = t.name
    form.task_type = (t.task_type as TaskType) || 'EXPLORATORY'
    form.goal_template = t.goal_template
    form.variables = t.variables.map((v) => ({
      name: v.name,
      label: v.label ?? '',
      required: Boolean(v.required),
    }))
    form.fields = t.field_schema.map((f) => ({
      name: String(f.name ?? ''),
      type: String(f.type ?? 'text'),
      required: Boolean(f.required),
    }))
    form.conditions = t.completion_conditions.map((c) => ({
      kind: String(c.kind ?? 'min_records'),
      target: typeof c.target === 'number' ? c.target : null,
    }))
    const adv = t.advanced_settings
    form.max_pages = typeof adv.max_pages === 'number' ? adv.max_pages : null
    form.max_duration_minutes =
      typeof adv.max_duration_minutes === 'number' ? adv.max_duration_minutes : null
    form.max_retries_per_url =
      typeof adv.max_retries_per_url === 'number' ? adv.max_retries_per_url : null
  } catch (err) {
    error.value = mapApiError(err).message
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  error.value = null
  try {
    if (templateId.value) {
      await updateTemplate(templateId.value, toBody())
    } else {
      await createTemplate(toBody())
    }
    void router.push('/templates')
  } catch (err) {
    error.value = mapApiError(err).message
  } finally {
    saving.value = false
  }
}

onMounted(() => void loadTemplate())
</script>

<template>
  <section class="page page--wide template-edit">
    <header class="page__header">
      <h1>{{ templateId ? '编辑模板' : '新建模板' }}</h1>
      <button v-if="templateId" type="button" class="ghost" @click="router.push('/templates')">
        ← 返回模板列表
      </button>
    </header>
    <p v-if="error" class="form-error">{{ error }}</p>
    <p v-if="loading" class="muted">加载中…</p>

    <div v-if="!loading" class="template-edit__form">
      <label class="field">
        <span>模板名称</span>
        <input v-model="form.name" class="input" />
      </label>

      <label class="field">
        <span>任务类型</span>
        <select v-model="form.task_type" class="input">
          <option v-for="(label, t) in TASK_TYPE_LABELS" :key="t" :value="t">{{ label }}</option>
        </select>
      </label>

      <label class="field">
        <span>目标模板（用 {变量名} 占位，如：帮我搜集{city}的工业自动化设备供应商）</span>
        <textarea v-model="form.goal_template" rows="3" class="input" />
      </label>

      <div class="section">
        <div class="section__head">
          <span>模板变量</span>
          <button type="button" class="ghost" @click="addVar">+ 添加变量</button>
        </div>
        <div v-for="idx in form.variables.length" :key="idx" class="row">
          <input
            v-model="form.variables[idx - 1].name"
            class="input row__name"
            placeholder="变量名（如 city）"
          />
          <input
            v-model="form.variables[idx - 1].label"
            class="input row__name"
            placeholder="标签（如 城市）"
          />
          <label class="row__check"
            ><input v-model="form.variables[idx - 1].required" type="checkbox" /> 必填</label
          >
          <button type="button" class="ghost" @click="removeVar(idx - 1)">删除</button>
        </div>
      </div>

      <div class="section">
        <div class="section__head">
          <span>字段定义</span>
          <button type="button" class="ghost" @click="addField">+ 添加字段</button>
        </div>
        <div v-for="idx in form.fields.length" :key="idx" class="row">
          <input v-model="form.fields[idx - 1].name" class="input row__name" placeholder="字段名" />
          <select v-model="form.fields[idx - 1].type" class="input row__type">
            <option v-for="t in FIELD_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
          <label class="row__check"
            ><input v-model="form.fields[idx - 1].required" type="checkbox" /> 必填</label
          >
          <button type="button" class="ghost" @click="removeField(idx - 1)">删除</button>
        </div>
      </div>

      <div class="section">
        <div class="section__head">
          <span>完成规则</span>
          <button type="button" class="ghost" @click="addCondition">+ 添加</button>
        </div>
        <div v-for="idx in form.conditions.length" :key="idx" class="row">
          <select v-model="form.conditions[idx - 1].kind" class="input row__type">
            <option value="min_records">最低记录数</option>
            <option value="range_covered">范围覆盖</option>
            <option value="saturation">信息饱和</option>
            <option value="limit">运行限制</option>
          </select>
          <input
            v-model="form.conditions[idx - 1].target"
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
            <input v-model="form.max_pages" type="number" class="input" />
          </label>
          <label class="field">
            <span>最长运行时间（分钟）</span>
            <input v-model="form.max_duration_minutes" type="number" class="input" />
          </label>
          <label class="field">
            <span>单 URL 最大重试次数</span>
            <input v-model="form.max_retries_per_url" type="number" class="input" />
          </label>
        </div>
      </details>

      <div class="template-edit__actions">
        <button type="button" class="ghost" @click="router.push('/templates')">取消</button>
        <button type="button" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存模板' }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.template-edit__form {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  max-width: 720px;
}
.page__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.input {
  padding: 0.5rem 0.6rem;
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
.row__check {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  white-space: nowrap;
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
.template-edit__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.template-edit__actions button {
  padding: 0.5rem 1.2rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
.template-edit__actions button.ghost {
  background: transparent;
  color: var(--color-text);
}
.template-edit__actions button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.form-error {
  color: var(--color-danger);
  font-size: 0.85rem;
}
</style>
