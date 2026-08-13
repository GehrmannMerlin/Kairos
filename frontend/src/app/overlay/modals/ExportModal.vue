<script setup lang="ts">
// Export Modal（D-060/D-067）：选择导出类型 + 范围 → POST /artifacts/export。
import { computed, ref } from 'vue'

import { closeModal } from '@/app/overlay/modal.store'
import { artifactDownloadUrl, exportArtifact } from '@/features/artifacts/artifacts.api'
import type { ExportRequest, ExportType } from '@/features/artifacts/types'

interface ExportPayload {
  taskId: string | number
  filter?: Record<string, unknown>
}
const props = defineProps<{ payload?: ExportPayload }>()

const EXPORT_OPTIONS: { value: ExportType; label: string; hint: string }[] = [
  { value: 'formal', label: '正式 CSV', hint: '只包含已通过记录' },
  { value: 'review', label: '待复核 CSV', hint: '只包含待复核记录，含复核原因' },
  { value: 'audit', label: '审核完整 CSV', hint: '含已通过/待复核/已拒绝与状态字段' },
]

const exportType = ref<ExportType>('formal')
const scope = ref<'current' | 'all'>('all')
const running = ref(false)
const error = ref<string | null>(null)
const success = ref<{ url: string; rows: number } | null>(null)

const hasFilter = computed(() => {
  const f = props.payload?.filter ?? {}
  return Object.values(f).some((v) => v !== undefined && v !== null && v !== '')
})

async function run(): Promise<void> {
  if (!props.payload?.taskId) return
  running.value = true
  error.value = null
  success.value = null
  try {
    const filter = props.payload.filter ?? {}
    const request: ExportRequest = {
      export_type: exportType.value,
      scope: hasFilter.value && scope.value === 'current' ? 'current' : 'all',
      filter,
    }
    const ref = await exportArtifact(props.payload.taskId, request)
    success.value = {
      url: artifactDownloadUrl(props.payload.taskId, ref.artifact_id),
      rows: ref.row_count,
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div class="export-form" data-testid="export-modal">
    <fieldset class="export-form__group">
      <legend>导出类型</legend>
      <label
        v-for="opt in EXPORT_OPTIONS"
        :key="opt.value"
        class="export-form__option"
      >
        <input
          v-model="exportType"
          type="radio"
          name="export_type"
          :value="opt.value"
          data-testid="export-type"
        />
        <span>
          <strong>{{ opt.label }}</strong>
          <span class="muted">{{ opt.hint }}</span>
        </span>
      </label>
    </fieldset>

    <fieldset class="export-form__group">
      <legend>范围</legend>
      <label class="export-form__option">
        <input
          v-model="scope"
          type="radio"
          name="export_scope"
          value="all"
          data-testid="export-scope-all"
        />
        <span><strong>全部当前分区</strong></span>
      </label>
      <label class="export-form__option" :class="{ 'export-form__option--disabled': !hasFilter }">
        <input
          v-model="scope"
          type="radio"
          name="export_scope"
          value="current"
          data-testid="export-scope-current"
          :disabled="!hasFilter"
        />
        <span><strong>当前筛选结果</strong></span>
      </label>
    </fieldset>

    <p v-if="error" class="form-error">{{ error }}</p>

    <p v-if="success" class="export-form__success" data-testid="export-success">
      已生成 {{ success.rows }} 行
      <a :href="success.url" data-testid="export-download-link">下载 CSV</a>
    </p>

    <div class="export-form__actions">
      <button type="button" class="ghost" @click="closeModal">关闭</button>
      <button type="button" :disabled="running" @click="run">
        {{ running ? '生成中…' : '导出并下载' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.export-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.export-form__group {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.6rem 0.75rem;
  margin: 0;
}
.export-form__group legend {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.export-form__option {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  padding: 0.3rem 0;
  cursor: pointer;
}
.export-form__option--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.export-form__option span {
  display: flex;
  flex-direction: column;
  font-size: 0.9rem;
}
.export-form__success {
  color: #2e7d32;
  font-size: 0.9rem;
}
.export-form__success a {
  color: #2563eb;
}
.export-form__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
