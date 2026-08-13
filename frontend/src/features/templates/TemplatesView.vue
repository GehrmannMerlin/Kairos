<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { mapApiError } from '@/app/error/apiErrorMapper'
import { openModal } from '@/app/overlay/modal.store'
import {
  deleteTemplate,
  duplicateTemplate,
  listTemplates,
  setTemplateFavorite,
  useTemplate,
  type TemplateDto,
} from '@/features/templates/templates.api'
import { TASK_TYPE_LABELS, type TaskType } from '@/features/tasks/spec.types'

const router = useRouter()
const templates = ref<TemplateDto[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

function typeLabel(t: TemplateDto): string {
  return TASK_TYPE_LABELS[t.task_type as TaskType] ?? t.task_type
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    templates.value = (await listTemplates()).templates
  } catch (err) {
    error.value = mapApiError(err).message
  } finally {
    loading.value = false
  }
}

async function onUse(t: TemplateDto): Promise<void> {
  if (t.variables.length) {
    openModal('TEMPLATE_VARIABLES', { template: t })
    return
  }
  try {
    const result = await useTemplate(t.template_id, {})
    void router.push(`/tasks/${result.task_id}/chat`)
  } catch (err) {
    error.value = mapApiError(err).message
  }
}

async function onDuplicate(t: TemplateDto): Promise<void> {
  try {
    await duplicateTemplate(t.template_id)
    await load()
  } catch (err) {
    error.value = mapApiError(err).message
  }
}

async function onFavorite(t: TemplateDto): Promise<void> {
  try {
    await setTemplateFavorite(t.template_id, !t.is_favorite)
    await load()
  } catch (err) {
    error.value = mapApiError(err).message
  }
}

async function onDelete(t: TemplateDto): Promise<void> {
  if (!window.confirm(`删除模板「${t.name}」？历史任务仍引用旧版本。`)) return
  try {
    await deleteTemplate(t.template_id)
    await load()
  } catch (err) {
    error.value = mapApiError(err).message
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>模板</h1>
    </header>
    <div class="toolbar">
      <button type="button" @click="router.push('/templates/new')">＋ 新建模板</button>
    </div>
    <p v-if="error" class="form-error">{{ error }}</p>
    <p v-if="loading" class="muted">加载中…</p>

    <table v-if="templates.length" class="template-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>任务类型</th>
          <th>版本</th>
          <th>常用</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in templates" :key="t.template_id">
          <td>{{ t.name }}</td>
          <td>{{ typeLabel(t) }}</td>
          <td>v{{ t.version }}</td>
          <td>{{ t.is_favorite ? '★' : '' }}</td>
          <td class="actions">
            <button type="button" @click="onUse(t)">使用</button>
            <button type="button" @click="router.push(`/templates/${t.template_id}/edit`)">
              编辑
            </button>
            <button type="button" @click="onDuplicate(t)">复制</button>
            <button type="button" @click="onFavorite(t)">
              {{ t.is_favorite ? '取消常用' : '设为常用' }}
            </button>
            <button type="button" class="danger" @click="onDelete(t)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="!loading" class="empty">暂无模板，从任务或新建创建。</p>
  </section>
</template>

<style scoped>
.toolbar {
  margin-bottom: 0.75rem;
}
.toolbar button {
  padding: 0.45rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
}
.template-table {
  width: 100%;
  border-collapse: collapse;
}
.template-table th,
.template-table td {
  border: 1px solid var(--color-border);
  padding: 0.5rem;
  text-align: left;
  font-size: 0.9rem;
}
.actions button {
  margin-right: 0.25rem;
  padding: 0.2rem 0.5rem;
  font-size: 0.8rem;
  cursor: pointer;
}
.danger {
  color: var(--color-danger);
}
.muted {
  color: var(--color-text-secondary);
}
.form-error {
  color: var(--color-danger);
  font-size: 0.85rem;
}
</style>
