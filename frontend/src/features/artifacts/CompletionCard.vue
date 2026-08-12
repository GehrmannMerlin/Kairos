<script setup lang="ts">
// Chat 完成总结卡（D-006/D-043）：NORMAL/PARTIAL 全部来自后端 DB facts，无假百分比。
import { computed } from 'vue'

import { openModal } from '@/app/overlay/modal.store'
import type { CompletionCardView } from '@/features/artifacts/types'

const props = defineProps<{ card: CompletionCardView; taskId: string }>()

const counts = computed(() => props.card.partition_counts)
const meta = computed(() => props.card.scope_completion_metadata as Record<string, unknown>)

function openExport(): void {
  openModal('EXPORT', { taskId: props.taskId, filter: {} })
}
</script>

<template>
  <section class="completion-card" data-testid="completion-card">
    <header class="completion-card__head">
      <h3>{{ card.is_partial ? '部分完成' : '任务已完成' }}</h3>
      <span class="completion-card__badge" :class="card.is_partial ? 'badge--partial' : 'badge--normal'">
        {{ card.is_partial ? 'PARTIAL' : 'NORMAL' }}
      </span>
    </header>

    <dl class="completion-card__stats">
      <div class="completion-card__stat">
        <dt>已通过</dt>
        <dd>{{ counts.passed ?? 0 }}</dd>
      </div>
      <div class="completion-card__stat">
        <dt>待复核</dt>
        <dd>{{ counts.needs_review ?? 0 }}</dd>
      </div>
      <div class="completion-card__stat">
        <dt>已拒绝</dt>
        <dd>{{ counts.rejected ?? 0 }}</dd>
      </div>
      <div class="completion-card__stat">
        <dt>来源/网页处理</dt>
        <dd>{{ card.url_processed }}</dd>
      </div>
    </dl>

    <p class="completion-card__reason">{{ card.reason }}</p>

    <!-- PARTIAL：停止原因 + 未覆盖/失败摘要（全部来自 DB facts，不虚构百分比） -->
    <template v-if="card.is_partial">
      <p v-if="card.runtime_limit_reason" class="completion-card__detail">
        停止原因：{{ card.runtime_limit_reason }}
      </p>
      <p v-if="meta.eligible_urls != null" class="completion-card__detail">
        未覆盖范围：已处理 {{ meta.terminal_urls ?? 0 }} / {{ meta.eligible_urls }} 个来源页面
      </p>
    </template>

    <div class="completion-card__actions">
      <RouterLink :to="`/tasks/${taskId}/data?status=passed`" class="completion-card__action">
        查看数据
      </RouterLink>
      <RouterLink :to="`/tasks/${taskId}/quality`" class="completion-card__action">
        查看质量
      </RouterLink>
      <button
        v-if="card.can_export_formal"
        type="button"
        class="completion-card__action"
        data-testid="completion-export"
        @click="openExport"
      >
        导出 CSV
      </button>
    </div>
  </section>
</template>

<style scoped>
.completion-card {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 1rem 1.1rem;
  background: var(--color-surface, #fff);
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.completion-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.completion-card__head h3 {
  margin: 0;
  font-size: 1rem;
}
.completion-card__badge {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
}
.badge--normal {
  background: #e8f5e9;
  color: #2e7d32;
}
.badge--partial {
  background: #fff3e0;
  color: #e65100;
}
.completion-card__stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.5rem;
  margin: 0;
}
.completion-card__stat {
  background: var(--color-surface-muted, rgba(0, 0, 0, 0.03));
  border-radius: 8px;
  padding: 0.5rem;
  text-align: center;
}
.completion-card__stat dt {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}
.completion-card__stat dd {
  margin: 0.2rem 0 0;
  font-size: 1.1rem;
  font-weight: 600;
}
.completion-card__reason {
  margin: 0;
  font-size: 0.9rem;
}
.completion-card__detail {
  margin: 0;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.completion-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.completion-card__action {
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.85rem;
  text-decoration: none;
}
.completion-card__action:hover {
  background: var(--color-surface-muted, rgba(0, 0, 0, 0.03));
}
</style>
