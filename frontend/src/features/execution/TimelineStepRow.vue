<script setup lang="ts">
import { computed } from 'vue'

import type { TimelineEvent } from './types'

const props = defineProps<{ event: TimelineEvent; active?: boolean }>()

const time = computed(() => {
  const date = new Date(props.event.timestamp)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
})

const running = computed(() =>
  ['RUNNING', 'WAITING_RESOURCE', 'WAITING_RETRY', 'PENDING'].includes(props.event.status ?? ''),
)
const failed = computed(() => ['FAILED', 'BLOCKED'].includes(props.event.status ?? ''))
</script>

<template>
  <li
    class="timeline-step-row"
    :class="{ 'timeline-step-row--active': active }"
    :data-status="event.status ?? ''"
  >
    <span
      class="step-status"
      :class="
        running ? 'step-status--running' : failed ? 'step-status--failed' : 'step-status--done'
      "
    >
      <span v-if="running" class="step-status__pulse" />
      <span v-else-if="failed" class="step-status__cross" />
      <span v-else class="step-status__check" />
    </span>
    <div class="step-body">
      <div class="step-line">
        <span class="step-summary">{{ event.summary }}</span>
        <span class="step-time">{{ time }}</span>
      </div>
      <div class="step-meta">
        <span v-if="event.node_id" class="step-chip">{{ event.node_id }}</span>
        <span v-if="event.retry_count > 0" class="step-chip">重试 {{ event.retry_count }}</span>
        <span v-if="event.error_code" class="step-chip step-chip--error">{{
          event.error_code
        }}</span>
        <span v-if="event.tool" class="step-chip">{{ event.tool }}</span>
        <span v-if="event.model" class="step-chip">{{ event.model }}</span>
        <span v-if="event.tokens_in != null || event.tokens_out != null" class="step-chip">
          tokens {{ event.tokens_in ?? 0 }}/{{ event.tokens_out ?? 0 }}
        </span>
        <span v-if="event.duration_ms != null" class="step-chip">{{ event.duration_ms }}ms</span>
      </div>
    </div>
  </li>
</template>

<style scoped>
.timeline-step-row {
  display: flex;
  gap: 0.6rem;
  align-items: flex-start;
  padding: 0.45rem 0.55rem;
  border-bottom: 1px solid var(--color-border);
  border-left: 2px solid transparent;
  font-size: 0.9rem;
}
.timeline-step-row--active {
  background: var(--color-bg-subtle, rgba(127, 127, 127, 0.08));
  border-left-color: var(--color-accent, #2563eb);
  border-radius: 6px;
}
.step-status {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.1rem;
  height: 1.1rem;
  margin-top: 0.15rem;
}
.step-status__pulse {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  background: var(--color-accent, #2563eb);
  animation: step-pulse 1.2s ease-in-out infinite;
}
.step-status__check::before {
  content: '✓';
  color: var(--color-success, #1a7f37);
  font-weight: 700;
}
.step-status__cross::before {
  content: '✕';
  color: var(--color-danger, #c62828);
  font-weight: 700;
}
@keyframes step-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
.step-body {
  flex: 1;
  min-width: 0;
}
.step-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
}
.step-summary {
  font-weight: 500;
}
.step-time {
  flex: none;
  color: var(--color-text-secondary);
  font-size: 0.78rem;
}
.step-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.25rem;
}
.step-chip {
  padding: 0.05rem 0.4rem;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-text-secondary);
  font-size: 0.72rem;
}
.step-chip--error {
  color: var(--color-danger, #c62828);
  border-color: var(--color-danger, #c62828);
}
</style>
