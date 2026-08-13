<script setup lang="ts">
// Node Detail Drawer（D-063/D-055）：只读节点执行详情。展示冻结定义 + 可获得的执行
// 证据 + 技术统计；无 Retry 命令时不显示重试按钮；token/duration 只是技术统计。
import { computed, ref } from 'vue'

import { getNodeDetail } from '@/features/execution/execution.api'
import type { NodeDetailDto } from '@/features/execution/types'

const props = defineProps<{ payload?: unknown }>()

const p = computed(() => (props.payload ?? {}) as { taskId?: string | number; nodeId?: string })
const taskId = computed(() => String(p.value.taskId ?? ''))
const nodeId = computed(() => String(p.value.nodeId ?? ''))

const detail = ref<NodeDetailDto | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    detail.value = await getNodeDetail(taskId.value, nodeId.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

if (taskId.value && nodeId.value) void load()

const params = computed(() => Object.entries(detail.value?.parameters_summary ?? {}))
</script>

<template>
  <div class="node-detail">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="detail">
      <p class="muted node-detail__meta">
        {{ detail.node_type }} · v{{ detail.definition_version }}
        <template v-if="detail.resource_class"> · {{ detail.resource_class }}</template>
      </p>
      <dl class="node-detail__dl">
        <dt>状态</dt>
        <dd>{{ detail.run?.state ?? '—' }}（Run #{{ detail.run?.run_id ?? '—' }}）</dd>
        <dt>计划版本</dt>
        <dd>v{{ detail.plan_version }}</dd>
        <dt>依赖</dt>
        <dd>{{ detail.depends_on.length ? detail.depends_on.join(' → ') : '无' }}</dd>
        <dt>可选 / 失败策略</dt>
        <dd>{{ detail.optional ? '可选' : '必需' }} / {{ detail.fail_policy }}</dd>
      </dl>

      <h4 class="node-detail__section">执行证据</h4>
      <dl class="node-detail__dl">
        <dt>事件</dt>
        <dd>{{ detail.execution.event_count }}</dd>
        <dt>尝试</dt>
        <dd>{{ detail.execution.attempt_count }}</dd>
        <dt>最近状态</dt>
        <dd>{{ detail.execution.last_status || '—' }}</dd>
        <dt>最近错误</dt>
        <dd class="node-detail__err">{{ detail.execution.last_error || '—' }}</dd>
        <dt>工具</dt>
        <dd>{{ detail.execution.tool || '—' }}</dd>
        <dt>抓取 URL</dt>
        <dd>{{ detail.execution.url_fetched_count }}</dd>
        <dt>记录数</dt>
        <dd>{{ detail.execution.record_count }}</dd>
        <dt>耗时 / Tokens</dt>
        <dd>
          {{ detail.execution.duration_ms != null ? `${detail.execution.duration_ms}ms` : '—' }} /
          {{ detail.execution.tokens_in ?? 0 }}→{{ detail.execution.tokens_out ?? 0 }}
        </dd>
      </dl>

      <h4 class="node-detail__section">参数摘要</h4>
      <dl v-if="params.length" class="node-detail__dl">
        <template v-for="[key, value] in params" :key="key">
          <dt>{{ key }}</dt>
          <dd>{{ String(value) }}</dd>
        </template>
      </dl>
      <p v-else class="muted">无参数</p>
    </template>
  </div>
</template>

<style scoped>
.node-detail__meta {
  margin-bottom: 0.6rem;
}
.node-detail__dl {
  display: grid;
  grid-template-columns: 7rem 1fr;
  gap: 0.3rem 0.6rem;
  margin: 0.5rem 0;
  font-size: 0.85rem;
}
.node-detail__dl dt {
  color: var(--color-text-secondary);
}
.node-detail__dl dd {
  margin: 0;
  word-break: break-word;
}
.node-detail__err {
  color: #dc2626;
}
.node-detail__section {
  margin: 0.75rem 0 0.3rem;
  font-size: 0.9rem;
  font-weight: 600;
}
</style>
