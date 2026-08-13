<script setup lang="ts">
// Evidence Quick Drawer（D-056/D-067）：快速核验字段证据；深度审查进完整证据查看器。
// 不重复整个 Full Viewer，不 fetch 当前网页冒充历史快照。
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { closeDrawer } from '@/app/overlay/drawer.store'
import { useEvidence } from '@/features/evidence/useEvidence'

const props = defineProps<{ payload?: unknown }>()
const router = useRouter()

const p = computed(() => (props.payload ?? {}) as { taskId?: string | number; evidenceId?: string | number })
const taskId = computed(() => String(p.value.taskId ?? ''))
const snapshotId = computed(() => String(p.value.evidenceId ?? ''))

const { view, loading, error } = useEvidence(taskId, snapshotId)

const first = computed(() => view.value?.field_evidence[0] ?? null)

function openFull(): void {
  closeDrawer()
  void router.push(`/tasks/${taskId.value}/evidence/${snapshotId.value}`)
}
</script>

<template>
  <div class="quick-evidence">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="view">
      <p class="muted quick-evidence__meta">
        字段：{{ first?.field_name ?? '—' }} · {{ view.display_mode }}
      </p>
      <p class="quick-evidence__value">{{ first?.value || '—' }}</p>
      <p v-if="first?.raw_snippet" class="muted quick-evidence__snippet">{{ first.raw_snippet }}</p>
      <p class="muted quick-evidence__meta">
        来源：{{ view.source_url || '—' }}
        <template v-if="first?.extract_method"> · {{ first.extract_method }}</template>
        <template v-if="first?.extractor_version"> v{{ first.extractor_version }}</template>
        <template v-if="first?.confidence != null"> · 置信度 {{ first.confidence }}</template>
      </p>
      <button type="button" class="quick-evidence__link" @click="openFull">完整查看</button>
    </template>
  </div>
</template>

<style scoped>
.quick-evidence__meta {
  margin-bottom: 0.4rem;
  font-size: 0.85rem;
}
.quick-evidence__value {
  font-weight: 600;
  margin-bottom: 0.4rem;
  word-break: break-word;
}
.quick-evidence__snippet {
  margin-bottom: 0.6rem;
}
.quick-evidence__link {
  border: none;
  background: none;
  color: var(--color-accent, #2563eb);
  cursor: pointer;
  padding: 0;
  font-size: 0.85rem;
}
</style>
