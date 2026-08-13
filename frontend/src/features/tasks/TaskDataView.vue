<script setup lang="ts">
// 数据工作区（D-041/D-044/D-060/D-062）。三分区 Tabs + 实时计数 + 搜索/筛选/排序/列设置 + 表格。
// 真实数据来自 Records Query API；SSE record.* 事件增量刷新（D-040）。
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { openDrawer } from '@/app/overlay/drawer.store'
import { openModal } from '@/app/overlay/modal.store'
import { useBatchReview, type BatchAction } from '@/features/data/useBatchReview'
import { useRecordEvents } from '@/features/data/useRecordEvents'
import { useRecords } from '@/features/data/useRecords'
import type { RecordListParams } from '@/features/data/types'
import type { RecordPartition, RecordView } from '@/features/data/types'

const route = useRoute()
const taskId = computed(() => String(route.params.taskId))

const {
  tab,
  items,
  total,
  partitionCounts,
  loading,
  error,
  page,
  search,
  params,
  load,
  setTab,
  setSearch,
  applyParams,
} = useRecords(taskId)

// Deep Link 回读（D-062）：?status=&review_type=&source_type=&extract_method=&q= 落到对应筛选
watch(
  () => route.query,
  (q) => {
    if (
      typeof q.status === 'string' &&
      (q.status === 'passed' || q.status === 'review' || q.status === 'rejected')
    ) {
      setTab(q.status === 'review' ? 'needs_review' : (q.status as RecordPartition))
    }
    if (typeof q.q === 'string' && q.q) {
      setSearch(q.q)
    }
    const p: RecordListParams = {}
    if (typeof q.review_type === 'string') p.review_type = q.review_type
    if (typeof q.source_type === 'string') p.source_type = q.source_type
    if (typeof q.extract_method === 'string') p.extract_method = q.extract_method
    if (Object.keys(p).length) applyParams(p)
  },
  { immediate: true },
)

// 运行中新增记录（D-040）：record.* SSE → 重新拉取
useRecordEvents(taskId, () => void load())

const TABS = [
  { key: 'all', label: '全部' },
  { key: 'passed', label: '已通过' },
  { key: 'needs_review', label: '待复核' },
  { key: 'rejected', label: '已拒绝' },
] as const

function countFor(key: string): number {
  if (key === 'all') return total.value
  return partitionCounts.value[key] ?? 0
}

// ---- 动态字段列 + 列设置（D-060：只影响 UI 显示）----
const SKIP_KEYS = new Set([
  'source_type',
  'extract_method',
  'confidence',
  'snapshot_id',
  'rule_versions',
  'recompute_eligible',
])
const fieldColumns = computed<string[]>(() => {
  const first = items.value[0]
  if (!first) return []
  return Object.keys(first.fields).filter((k) => !SKIP_KEYS.has(k)).slice(0, 6)
})
const visibleColumns = ref<string[]>([])
watch(
  fieldColumns,
  (cols) => {
    if (visibleColumns.value.length === 0) visibleColumns.value = cols
  },
  { immediate: true },
)
const showColumnSettings = ref(false)

function toggleColumn(col: string): void {
  visibleColumns.value = visibleColumns.value.includes(col)
    ? visibleColumns.value.filter((c) => c !== col)
    : [...visibleColumns.value, col]
}

// ---- 字段筛选（简单 AND，D-060）----
const filterField = ref('')
const filterValue = ref('')
const filterOptions = computed(() => fieldColumns.value)

function applyFilter(): void {
  if (filterField.value && filterValue.value !== '') {
    applyParams({ field: filterField.value, value: filterValue.value })
  } else {
    applyParams({ field: undefined, value: undefined })
  }
}

// ---- 排序（仅可排序字段，D-060）----
const sortBy = ref('created_at')
const sortOrder = ref<'asc' | 'desc'>('desc')

function applySort(): void {
  applyParams({ sort_by: sortBy.value, sort_order: sortOrder.value })
}

// ---- 分页 ----
const pageSize = 20
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const canPrev = computed(() => page.value > 1)
const canNext = computed(() => page.value < totalPages.value)

function nextPage(): void {
  if (canNext.value) {
    page.value += 1
    void load()
  }
}
function prevPage(): void {
  if (canPrev.value) {
    page.value -= 1
    void load()
  }
}

function openRecord(record: RecordView): void {
  openDrawer('RECORD', { taskId: taskId.value, recordId: record.record_id })
}

// ---- 导出（D-060）：Export Modal 复用当前筛选快照，UI 列设置不影响 CSV schema ----
function currentFilterSnapshot(): Record<string, unknown> {
  const p = params.value
  return {
    q: search.value || undefined,
    field: p.field ?? undefined,
    value: p.value ?? undefined,
    source_type: p.source_type ?? undefined,
    extract_method: p.extract_method ?? undefined,
    min_confidence: p.min_confidence ?? undefined,
    review_type: p.review_type ?? undefined,
  }
}

function openExport(): void {
  openModal('EXPORT', { taskId: taskId.value, filter: currentFilterSnapshot() })
}

// ---- 批量审核（D-061：按钮仅对后端 allowed_actions 允许且语义兼容记录开放；后端最终校验）----
const selected = ref<Map<number, number>>(new Map())
const selectedIds = computed<number[]>(() => [...selected.value.keys()])
const selectedVersions = computed<Record<number, number>>(() =>
  Object.fromEntries(selected.value.entries()),
)

function toggleSelect(record: RecordView): void {
  const next = new Map(selected.value)
  if (next.has(record.record_id)) next.delete(record.record_id)
  else next.set(record.record_id, record.data_version)
  selected.value = next
}

const batchAllowed = computed(() => {
  const selectedRecords = items.value.filter((r) => selected.value.has(r.record_id))
  return (action: string) =>
    selectedRecords.length > 0 && selectedRecords.every((r) => r.allowed_actions.includes(action))
})

const {
  pending: batchPending,
  error: batchError,
  run: runBatchAction,
} = useBatchReview(taskId.value, selectedIds, selectedVersions, () => {
  selected.value = new Map()
  void load()
})

function runBatch(action: BatchAction): void {
  void runBatchAction(action, '')
}

function displayField(record: RecordView, col: string): string {
  const v = record.fields[col]
  return v === null || v === undefined ? '—' : String(v)
}
</script>

<template>
  <section class="task-workspace">
    <nav class="data-tabs" aria-label="数据分区">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        class="data-tab"
        :class="{ 'data-tab--active': tab === t.key }"
        @click="setTab(t.key)"
      >
        {{ t.label }} <span class="data-tab__count">{{ countFor(t.key) }}</span>
      </button>
    </nav>

    <div class="data-toolbar">
      <input
        v-model="search"
        class="data-search"
        type="search"
        placeholder="搜索标题 / 文号 / 摘要"
        @input="setSearch(search)"
      />
      <select v-model="filterField" class="data-select" aria-label="筛选字段" @change="applyFilter">
        <option value="">筛选字段</option>
        <option v-for="col in filterOptions" :key="col" :value="col">{{ col }}</option>
      </select>
      <input
        v-model="filterValue"
        class="data-search"
        type="text"
        placeholder="字段值"
        @keyup.enter="applyFilter"
      />
      <select v-model="sortBy" class="data-select" aria-label="排序字段" @change="applySort">
        <option value="created_at">创建时间</option>
        <option value="updated_at">更新时间</option>
        <option value="id">ID</option>
      </select>
      <select v-model="sortOrder" class="data-select" aria-label="排序方向" @change="applySort">
        <option value="desc">降序</option>
        <option value="asc">升序</option>
      </select>
      <button type="button" class="data-settings" @click="showColumnSettings = !showColumnSettings">
        列设置
      </button>
      <button type="button" class="data-settings" data-testid="export-button" @click="openExport">
        导出
      </button>
    </div>

    <div v-if="showColumnSettings" class="data-colsettings" data-testid="column-settings">
      <label v-for="col in fieldColumns" :key="col" class="data-colcheck">
        <input type="checkbox" :checked="visibleColumns.includes(col)" @change="toggleColumn(col)" />
        {{ col }}
      </label>
    </div>

    <div v-if="selectedIds.length" class="data-batchbar">
      <span class="muted">已选 {{ selectedIds.length }} 条</span>
      <button
        v-if="batchAllowed('approve')"
        type="button"
        :disabled="batchPending"
        @click="runBatch('approve')"
      >
        批量通过
      </button>
      <button
        v-if="batchAllowed('reject')"
        type="button"
        :disabled="batchPending"
        @click="runBatch('reject')"
      >
        批量拒绝
      </button>
      <button
        v-if="batchAllowed('agent_reevaluate')"
        type="button"
        :disabled="batchPending"
        @click="runBatch('agent_reevaluate')"
      >
        批量重新处理
      </button>
      <button type="button" @click="selected = new Map()">取消</button>
      <span v-if="batchError" class="data-batchbar__error">{{ batchError }}</span>
    </div>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <p v-else-if="items.length === 0" class="empty">暂无数据</p>

    <table v-else class="data-table">
      <thead>
        <tr>
          <th class="data-cell--select" aria-label="选择"></th>
          <th v-for="col in visibleColumns" :key="col">{{ col }}</th>
          <th>分区</th>
          <th>来源</th>
          <th>更新时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in items" :key="r.record_id" class="data-row" @click="openRecord(r)">
          <td class="data-cell--select" @click.stop>
            <input
              type="checkbox"
              :checked="selected.has(r.record_id)"
              :aria-label="`选择记录 ${r.record_id}`"
              @change="toggleSelect(r)"
            />
          </td>
          <td v-for="col in visibleColumns" :key="col">{{ displayField(r, col) }}</td>
          <td>{{ r.partition }}</td>
          <td class="data-cell--muted">{{ r.source_url }}</td>
          <td class="data-cell--muted">{{ new Date(r.updated_at).toLocaleString() }}</td>
        </tr>
      </tbody>
    </table>

    <div v-if="items.length" class="data-pager">
      <button type="button" :disabled="!canPrev" @click="prevPage">上一页</button>
      <span class="muted">{{ page }} / {{ totalPages }}（共 {{ total }} 条）</span>
      <button type="button" :disabled="!canNext" @click="nextPage">下一页</button>
    </div>
  </section>
</template>

<style scoped>
.data-tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.data-tab {
  padding: 0.35rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.data-tab--active {
  background: var(--color-accent, #2563eb);
  color: #fff;
  border-color: transparent;
}
.data-tab__count {
  font-weight: 600;
}
.data-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.75rem;
}
.data-search {
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
}
.data-select {
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
}
.data-settings {
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.data-colsettings {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin-bottom: 0.6rem;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
}
.data-colcheck {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.data-table {
  width: 100%;
  border-collapse: collapse;
}
.data-table th,
.data-table td {
  padding: 0.45rem 0.7rem;
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  font-size: 0.9rem;
}
.data-row {
  cursor: pointer;
}
.data-row:hover {
  background: var(--color-surface-hover, rgba(0, 0, 0, 0.03));
}
.data-cell--muted {
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}
.data-pager {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.75rem;
}
.data-cell--select {
  width: 2rem;
  text-align: center;
}
.data-batchbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface, transparent);
}
.data-batchbar__error {
  color: #dc2626;
  font-size: 0.85rem;
}
</style>
