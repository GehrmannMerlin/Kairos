<script setup lang="ts">
import { computed } from 'vue'

import { TASK_TYPE_LABELS, type SpecDraftPayload, type TaskType } from '@/features/tasks/spec.types'

// D-035：CollectionSpec 摘要确认卡。展示真实结构化 Draft；「查看/修改采集方案」
// 打开完整 Editor Sheet；「确认并执行」→ confirm（DRAFT->QUEUED，冻结版本）。
// 本卡不伪造执行中状态（真实执行在 M-07/M-08 接入）。
const props = defineProps<{
  payload: SpecDraftPayload
  confirmedVersion?: number | null
  confirming?: boolean
}>()

const emit = defineEmits<{ 'open-editor': []; confirm: [] }>()

const taskTypeLabel = computed(() =>
  props.payload.task_type
    ? (TASK_TYPE_LABELS[props.payload.task_type as TaskType] ?? props.payload.task_type)
    : '未识别',
)
</script>

<template>
  <div class="spec-card">
    <header class="spec-card__head">
      <span class="spec-card__type">{{ taskTypeLabel }}</span>
      <span v-if="confirmedVersion" class="spec-card__version">已确认 v{{ confirmedVersion }}</span>
      <span v-else class="spec-card__version">草稿</span>
    </header>

    <h4 class="spec-card__goal">{{ payload.goal || '（未填写目标）' }}</h4>

    <div v-if="payload.fields?.length" class="spec-card__block">
      <div class="spec-card__label">字段</div>
      <ul class="spec-card__fields">
        <li v-for="f in payload.fields" :key="f.name">
          <span>{{ f.name }}</span>
          <span class="muted"> {{ f.type }}{{ f.required ? ' · 必填' : '' }}</span>
        </li>
      </ul>
    </div>

    <div v-if="payload.source_scope?.seed_urls?.length" class="spec-card__block">
      <div class="spec-card__label">范围</div>
      <ul class="spec-card__urls">
        <li v-for="u in payload.source_scope.seed_urls" :key="u">{{ u }}</li>
      </ul>
    </div>

    <div v-if="payload.completion_conditions?.length" class="spec-card__block">
      <div class="spec-card__label">完成条件</div>
      <p class="spec-card__completion">
        {{
          payload.completion_conditions
            .map((c) => `${c.kind}${c.target ? '·' + c.target : ''}`)
            .join('；')
        }}
      </p>
    </div>

    <p v-if="payload.auto_expand_fields" class="muted">自动扩展可选字段：已开启</p>

    <footer class="spec-card__actions">
      <button type="button" class="ghost" @click="emit('open-editor')">查看 / 修改采集方案</button>
      <button
        v-if="!confirmedVersion"
        type="button"
        :disabled="confirming"
        @click="emit('confirm')"
      >
        {{ confirming ? '确认中…' : '确认并执行' }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.spec-card {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 0.9rem 1rem;
  background: var(--color-surface);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.spec-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}
.spec-card__type {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--color-text);
}
.spec-card__version {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}
.spec-card__goal {
  margin: 0;
  font-size: 1rem;
}
.spec-card__block {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.spec-card__label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
}
.spec-card__fields,
.spec-card__urls {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 1rem;
}
.spec-card__fields li,
.spec-card__urls li {
  font-size: 0.85rem;
}
.spec-card__completion {
  margin: 0;
  font-size: 0.85rem;
}
.spec-card__actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}
button {
  padding: 0.4rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-text);
  color: var(--color-bg);
  cursor: pointer;
  font-size: 0.85rem;
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
  font-size: 0.8rem;
}
</style>
