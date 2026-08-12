<script setup lang="ts">
// Record Detail Drawer（D-041/D-042）：字段最终值 + 来源/证据元数据 + 单条审核动作。
// allowed_actions 来自后端；字段证据保留原值来源，人工修正写入 USER_OVERRIDE。
import { computed, ref } from 'vue'

import { openDrawer } from '@/app/overlay/drawer.store'
import { useRecordDetail } from '@/features/data/useRecordDetail'
import type { RecordFieldDetail } from '@/features/data/types'

const props = defineProps<{ payload?: unknown }>()

const p = computed(() => (props.payload ?? {}) as { taskId?: string | number; recordId?: number })
const taskId = computed(() => String(p.value.taskId ?? ''))
const recordId = computed(() => Number(p.value.recordId))

const { detail, loading, error, can, approve, reject, edit, reprocess } = useRecordDetail(
  taskId.value,
  recordId.value,
)

const editingField = ref<RecordFieldDetail | null>(null)
const editValue = ref('')
const reviewReason = ref('')
const busy = ref(false)
const actionError = ref<string | null>(null)

function beginEdit(field: RecordFieldDetail): void {
  editingField.value = field
  editValue.value = field.value ?? ''
}

async function run(action: () => Promise<void>): Promise<void> {
  busy.value = true
  actionError.value = null
  try {
    await action()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function saveEdit(): Promise<void> {
  if (!editingField.value) return
  await run(() =>
    edit([{ field_name: editingField.value!.field_name, final_value: editValue.value }]),
  )
  editingField.value = null
}

const evidenceSnapshotId = computed(() => {
  if (!detail.value) return null
  for (const f of detail.value.fields) {
    if (f.snapshot_id != null) return f.snapshot_id
  }
  return null
})
</script>

<template>
  <div class="record-drawer">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="detail">
      <p class="muted record-drawer__meta">
        状态：{{ detail.partition }}
        <template v-if="detail.review_type"> · {{ detail.review_type }}：{{ detail.review_reason }}</template>
      </p>

      <table class="record-fields">
        <tbody>
          <tr v-for="f in detail.fields" :key="f.field_name">
            <th class="record-fields__name">{{ f.field_name }}</th>
            <td>
              <div v-if="editingField?.field_name === f.field_name" class="record-edit">
                <input v-model="editValue" type="text" class="record-edit__input" />
                <button type="button" :disabled="busy" @click="saveEdit">保存</button>
                <button type="button" @click="editingField = null">取消</button>
              </div>
              <template v-else>
                <span :class="{ 'record-field--override': f.value_source === 'USER_OVERRIDE' }">
                  {{ f.value || '—' }}
                </span>
                <span
                  v-if="f.value_source === 'USER_OVERRIDE'"
                  class="muted"
                >（人工修正，原值：{{ f.original_value }}）</span>
                <button v-if="can('edit')" type="button" class="record-fields__link" @click="beginEdit(f)">
                  修正
                </button>
                <p v-if="f.extract_method" class="muted record-fields__meta">
                  来源：{{ f.extract_method }} v{{ f.extractor_version }} · 置信度 {{ f.confidence }}
                </p>
              </template>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="actionError" class="empty record-drawer__error">{{ actionError }}</p>

      <div class="record-actions">
        <label class="record-actions__reason">
          复核原因
          <input v-model="reviewReason" type="text" class="record-actions__input" />
        </label>
        <button v-if="can('approve')" type="button" :disabled="busy" @click="run(() => approve(reviewReason || undefined))">
          通过
        </button>
        <button v-if="can('reject')" type="button" :disabled="busy" @click="run(() => reject(reviewReason || undefined))">
          拒绝
        </button>
        <button v-if="can('agent_reevaluate')" type="button" :disabled="busy" @click="run(() => reprocess(reviewReason || undefined))">
          让 Agent 重新处理
        </button>
        <button v-if="can('resolve_conflict')" type="button" class="muted" disabled title="冲突裁决在后续模块接入">
          冲突裁决
        </button>
        <button v-if="can('merge_duplicate')" type="button" class="muted" disabled title="合并重复在后续模块接入">
          合并重复
        </button>
      </div>

      <button
        v-if="evidenceSnapshotId != null"
        type="button"
        class="record-fields__link"
        @click="openDrawer('EVIDENCE_QUICK', { taskId, evidenceId: evidenceSnapshotId })"
      >
        查看网页证据
      </button>
    </template>
  </div>
</template>

<style scoped>
.record-drawer__meta {
  margin-bottom: 0.75rem;
}
.record-fields {
  width: 100%;
  border-collapse: collapse;
}
.record-fields th,
.record-fields td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  vertical-align: top;
}
.record-fields__name {
  width: 30%;
  font-weight: 500;
  color: var(--color-text-secondary);
}
.record-fields__meta {
  font-size: 0.8rem;
  margin-top: 0.2rem;
}
.record-fields__link {
  border: none;
  background: none;
  color: var(--color-accent, #2563eb);
  cursor: pointer;
  padding: 0;
  margin-left: 0.4rem;
  font-size: 0.85rem;
}
.record-field--override {
  font-weight: 600;
}
.record-edit {
  display: flex;
  gap: 0.4rem;
  align-items: center;
}
.record-edit__input,
.record-actions__input {
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
}
.record-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin: 0.9rem 0;
}
.record-actions__reason {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.record-drawer__error {
  color: #dc2626;
}
</style>
