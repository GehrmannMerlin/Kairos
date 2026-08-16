<script setup lang="ts">
import { computed, onMounted, toRef, watch } from 'vue'

import { useTaskEvents } from '@/features/tasks/useTaskEvents'

import { useExecution } from './useExecution'

const props = defineProps<{ taskId: string | number }>()
const taskId = toRef(props, 'taskId')
const execution = useExecution(taskId)
const events = useTaskEvents(taskId)

const NODE_LABELS: Record<string, string> = {
  source_search: '解析指定来源',
  access_rules_check: '检查访问规则',
  link_discovery: '发现页面链接',
  fetch: '抓取页面',
  browser_render: '渲染动态页面',
  extract: '提取字段',
  normalize: '规范化数据',
  deduplicate: '去重与冲突检查',
  validate: '验证记录',
  generate_artifact: '生成结果文件',
}

const OUTCOME_LABELS: Record<string, string> = {
  NODE_EXECUTOR_UNAVAILABLE: '系统执行能力不可用，运行已失败',
  NO_MATCHING_PAGES: '执行完成：未发现符合条件的页面',
  NO_MATCHING_RECORDS: '执行完成：页面中未提取到符合条件的记录',
}

const WAITING_LABELS: Record<string, string> = {
  CREDENTIAL_REQUIRED: '等待提供访问凭据',
  RESOURCE_UNAVAILABLE: '等待执行资源',
  WAITING_APPROVAL: '等待人工确认',
  ACCESS_LIMITED: '访问受限，等待恢复',
}

const recentTimeline = computed(() => execution.timeline.value.slice(-5).reverse())
const currentLabel = computed(() => {
  const node = execution.view.value?.current_node
  return node ? (NODE_LABELS[node.node_type] ?? node.label ?? node.node_type) : null
})
const successfulLabel = computed(() => {
  const node = execution.view.value?.last_successful_node
  return node ? (NODE_LABELS[node.node_type] ?? node.label ?? node.node_type) : null
})
const outcomeText = computed(() => {
  const code = execution.view.value?.outcome_code
  return code ? (OUTCOME_LABELS[code] ?? code) : null
})
const outcomeIsFailure = computed(() => {
  const code = execution.view.value?.outcome_code
  return Boolean(code && code !== 'NO_MATCHING_PAGES' && code !== 'NO_MATCHING_RECORDS')
})
const waitingText = computed(() => {
  const code = execution.view.value?.waiting_reason_code
  return code ? (WAITING_LABELS[code] ?? code) : null
})

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }).format(date)
}

watch(events.latestEvent, () => {
  void execution.refreshSnapshot()
})

watch(events.reconcileVersion, (version, previous) => {
  if (version > previous) void execution.refreshSnapshot()
})

onMounted(events.connect)
</script>

<template>
  <section class="execution-progress" aria-label="执行进度">
    <header class="execution-progress__header">
      <div>
        <p class="execution-progress__eyebrow">执行进度</p>
        <h3>
          {{ currentLabel ?? (execution.view.value?.run ? '正在准备下一节点' : '尚未启动执行') }}
        </h3>
      </div>
      <span class="execution-progress__connection" :data-status="events.connection.value">
        {{ events.connection.value === 'reconnecting' ? '正在重连' : '实时更新' }}
      </span>
    </header>

    <p v-if="execution.loading.value && !execution.view.value" class="execution-progress__muted">
      正在读取执行快照…
    </p>
    <p v-else-if="execution.error.value" class="execution-progress__error">
      {{ execution.error.value }}
    </p>

    <template v-if="execution.view.value">
      <dl class="execution-progress__facts">
        <div>
          <dt>发现页面</dt>
          <dd>{{ execution.view.value.counts.discovered_pages }}</dd>
        </div>
        <div>
          <dt>已抓取</dt>
          <dd>{{ execution.view.value.counts.fetched_pages }}</dd>
        </div>
        <div>
          <dt>已提取</dt>
          <dd>{{ execution.view.value.counts.extracted_records }}</dd>
        </div>
        <div>
          <dt>已验证</dt>
          <dd>{{ execution.view.value.counts.validated_records }}</dd>
        </div>
      </dl>

      <div class="execution-progress__status">
        <p v-if="successfulLabel"><span>最近完成</span>{{ successfulLabel }}</p>
        <p><span>上次活动</span>{{ formatTime(execution.view.value.last_activity_at) }}</p>
      </div>

      <p
        v-if="outcomeText"
        class="execution-progress__message"
        :class="{ 'execution-progress__message--failure': outcomeIsFailure }"
      >
        {{ outcomeText }}
      </p>
      <p v-else-if="waitingText" class="execution-progress__message">{{ waitingText }}</p>
      <p
        v-else-if="execution.view.value.current_node?.safe_message"
        class="execution-progress__message"
      >
        {{ execution.view.value.current_node.safe_message }}
      </p>

      <ol
        v-if="recentTimeline.length"
        class="execution-progress__timeline"
        aria-label="最近执行事件"
      >
        <li v-for="item in recentTimeline" :key="item.event_id">
          <time :datetime="item.timestamp">{{ formatTime(item.timestamp) }}</time>
          <span>{{ item.summary }}</span>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
.execution-progress {
  display: grid;
  gap: 0.8rem;
  padding: 0.9rem 1rem;
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-primary);
  border-radius: 0.65rem;
  background: var(--color-surface);
}
.execution-progress__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
.execution-progress__eyebrow,
.execution-progress__header h3,
.execution-progress__status p,
.execution-progress__message {
  margin: 0;
}
.execution-progress__eyebrow {
  color: var(--color-text-secondary);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}
.execution-progress__header h3 {
  margin-top: 0.18rem;
  font-size: 1rem;
}
.execution-progress__connection {
  flex: none;
  color: var(--color-text-secondary);
  font-size: 0.72rem;
}
.execution-progress__connection[data-status='open']::before {
  content: '●';
  margin-right: 0.3rem;
  color: #2e7d32;
}
.execution-progress__facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.5rem;
  margin: 0;
}
.execution-progress__facts div {
  padding: 0.55rem 0.65rem;
  border-radius: 0.45rem;
  background: var(--color-bg-subtle, rgba(127, 127, 127, 0.08));
}
.execution-progress__facts dt {
  color: var(--color-text-secondary);
  font-size: 0.72rem;
}
.execution-progress__facts dd {
  margin: 0.15rem 0 0;
  font-size: 1.05rem;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}
.execution-progress__status {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem 1.2rem;
  font-size: 0.78rem;
}
.execution-progress__status span {
  margin-right: 0.4rem;
  color: var(--color-text-secondary);
}
.execution-progress__message {
  color: var(--color-text-secondary);
  font-size: 0.82rem;
}
.execution-progress__message--failure,
.execution-progress__error {
  color: var(--color-danger, #b42318);
}
.execution-progress__timeline {
  display: grid;
  gap: 0.35rem;
  margin: 0;
  padding: 0.6rem 0 0;
  border-top: 1px solid var(--color-border);
  list-style: none;
}
.execution-progress__timeline li {
  display: grid;
  grid-template-columns: 7.5rem minmax(0, 1fr);
  gap: 0.6rem;
  font-size: 0.75rem;
}
.execution-progress__timeline time,
.execution-progress__muted {
  color: var(--color-text-secondary);
}
@media (max-width: 640px) {
  .execution-progress__facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .execution-progress__timeline li {
    grid-template-columns: 1fr;
    gap: 0.1rem;
  }
}
</style>
