<script setup lang="ts">
// 执行详情二级页（D-055/D-063）：默认"阶段 + 时间线"，可切换只读 Plan DAG。
// 阶段/时间线全部来自后端 Execution/Timeline Query；Token 只作技术统计，无金额 UI。
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { openDrawer } from '@/app/overlay/drawer.store'
import { useExecution } from '@/features/execution/useExecution'
import type { DagNode, TimelineCategory } from '@/features/execution/types'

const route = useRoute()
const taskId = computed(() => String(route.params.taskId))

const {
  view,
  loading,
  error,
  timeline,
  timelineLoading,
  timelineError,
  filter,
  hasMore,
  viewMode,
  dag,
  dagLoading,
  dagError,
  loadMore,
  setFilter,
  toggleDag,
} = useExecution(taskId)

const STAGE_STATE_LABEL: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
}

const FILTERS: { key: TimelineCategory | ''; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'error', label: '错误' },
  { key: 'retry', label: '重试' },
  { key: 'tool_upgrade', label: '工具升级' },
  { key: 'plan_change', label: '计划调整' },
  { key: 'model_call', label: '模型调用' },
  { key: 'pause_resume', label: '暂停/恢复' },
]

function stageClass(state: string): string {
  return `stage-card--${state}`
}

// ---- 只读 DAG：按 depends_on 计算深度，分层渲染；点击 Node 打开 Node Detail Drawer ----
function nodeDepth(nodes: DagNode[]): Record<string, number> {
  const byId = new Map(nodes.map((n) => [n.node_id, n]))
  const depth: Record<string, number> = {}
  const compute = (id: string): number => {
    if (depth[id] !== undefined) return depth[id]
    const node = byId.get(id)
    if (!node || !node.depends_on.length) {
      depth[id] = 0
      return 0
    }
    depth[id] = Math.max(...node.depends_on.map(compute)) + 1
    return depth[id]
  }
  for (const n of nodes) compute(n.node_id)
  return depth
}

const dagColumns = computed<DagNode[][]>(() => {
  if (!dag.value || !dag.value.nodes.length) return []
  const depth = nodeDepth(dag.value.nodes)
  const maxDepth = Math.max(...dag.value.nodes.map((n) => depth[n.node_id] ?? 0))
  const columns: DagNode[][] = Array.from({ length: maxDepth + 1 }, () => [])
  for (const n of dag.value.nodes) {
    columns[depth[n.node_id] ?? 0].push(n)
  }
  return columns
})

function openNode(node: DagNode): void {
  openDrawer('NODE_DETAIL', { taskId: taskId.value, nodeId: node.node_id })
}

function nodeStatus(node: DagNode): string {
  return dag.value?.stage_status[node.stage] ?? 'not_started'
}
</script>

<template>
  <section class="task-workspace">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="view">
      <div class="exec-header">
        <p class="muted">
          运行状态：{{ view.run?.state ?? '—' }}
          <template v-if="view.run"> · Run #{{ view.run.run_id }} · 计划 v{{ view.plan?.plan_version }}</template>
        </p>
        <p class="muted">
          URL：发现 {{ view.urls.discovered ?? 0 }} / 抓取 {{ view.urls.fetched ?? 0 }} / 失败 {{ view.urls.failed ?? 0 }}
          <template v-if="view.records"> · 记录：通过 {{ view.records.passed ?? 0 }} / 待复核 {{ view.records.needs_review ?? 0 }}</template>
        </p>
        <button type="button" class="exec-toggle" @click="toggleDag">
          {{ viewMode === 'stage' ? '查看流程图' : '查看阶段' }}
        </button>
      </div>

      <!-- 阶段视图（默认） -->
      <template v-if="viewMode === 'stage'">
        <div class="stage-grid">
          <div
            v-for="stage in view.stages"
            :key="stage.key"
            class="stage-card"
            :class="stageClass(stage.state)"
            data-testid="stage-card"
          >
            <span class="stage-card__label">{{ stage.label }}</span>
            <span class="stage-card__state">{{ STAGE_STATE_LABEL[stage.state] }}</span>
            <span class="muted stage-card__meta">
              事件 {{ stage.event_count }}<template v-if="stage.url_processed"> · URL {{ stage.url_processed }}</template>
              <template v-if="stage.record_count"> · 记录 {{ stage.record_count }}</template>
              <template v-if="stage.error_count"> · 错误 {{ stage.error_count }}</template>
            </span>
          </div>
        </div>

        <h3 class="exec-section">时间线</h3>
        <div class="timeline-filters">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            type="button"
            class="timeline-filter"
            :class="{ 'timeline-filter--active': filter === f.key }"
            @click="setFilter(f.key)"
          >
            {{ f.label }}
          </button>
        </div>
        <p v-if="timelineLoading && timeline.length === 0" class="muted">加载时间线…</p>
        <p v-else-if="timelineError" class="empty">{{ timelineError }}</p>
        <ul v-else-if="timeline.length" class="timeline-list">
          <li v-for="ev in timeline" :key="ev.event_id" class="timeline-item">
            <span class="timeline-item__time">{{ new Date(ev.timestamp).toLocaleString() }}</span>
            <span class="timeline-item__summary">{{ ev.summary }}</span>
            <span class="timeline-item__stage muted">{{ ev.stage }}</span>
            <span v-if="ev.error_code" class="timeline-item__error">{{ ev.error_code }}</span>
            <span v-if="ev.tool" class="muted">· {{ ev.tool }}</span>
            <span v-if="ev.model" class="muted">· {{ ev.model }}</span>
            <span v-if="ev.tokens_in != null || ev.tokens_out != null" class="muted">
              · tokens {{ ev.tokens_in ?? 0 }}/{{ ev.tokens_out ?? 0 }}
            </span>
          </li>
        </ul>
        <p v-else class="muted">暂无时间线事件</p>
        <div v-if="hasMore" class="timeline-more">
          <button type="button" :disabled="timelineLoading" @click="loadMore">
            {{ timelineLoading ? '加载中…' : '加载更多' }}
          </button>
        </div>
      </template>

      <template v-else>
        <p v-if="dagLoading" class="muted">加载计划图…</p>
        <p v-else-if="dagError" class="empty">{{ dagError }}</p>
        <template v-else-if="dag">
          <p v-if="!dag.nodes.length" class="empty">暂无计划</p>
          <template v-else>
            <div class="dag-cols">
              <div v-for="(col, i) in dagColumns" :key="i" class="dag-col">
                <button
                  v-for="node in col"
                  :key="node.node_id"
                  type="button"
                  class="dag-node"
                  data-testid="dag-node"
                  @click="openNode(node)"
                >
                  <span class="dag-node__type">{{ node.node_type }}</span>
                  <span class="muted dag-node__meta">
                    {{ node.resource_class || '—' }} · {{ STAGE_STATE_LABEL[nodeStatus(node)] }}
                  </span>
                  <span v-if="node.optional" class="muted">（可选）</span>
                </button>
              </div>
            </div>
            <p class="muted dag-hint">点击节点查看详情（只读）</p>
          </template>
        </template>
      </template>
    </template>
  </section>
</template>

<style scoped>
.exec-header {
  margin-bottom: 0.75rem;
}
.exec-toggle {
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.stage-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr));
  gap: 0.6rem;
  margin-bottom: 1rem;
}
.stage-card {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.stage-card--completed {
  border-color: #16a34a;
}
.stage-card--failed {
  border-color: #dc2626;
}
.stage-card--in_progress {
  border-color: var(--color-accent, #2563eb);
}
.stage-card__label {
  font-weight: 600;
}
.stage-card__state {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.stage-card__meta {
  font-size: 0.8rem;
}
.exec-section {
  margin: 1rem 0 0.4rem;
  font-size: 0.95rem;
  font-weight: 600;
}
.timeline-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.6rem;
}
.timeline-filter {
  padding: 0.25rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}
.timeline-filter--active {
  background: var(--color-accent, #2563eb);
  color: #fff;
  border-color: transparent;
}
.timeline-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.timeline-item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--color-border);
  font-size: 0.9rem;
}
.timeline-item__time {
  color: var(--color-text-secondary);
  font-size: 0.8rem;
}
.timeline-item__summary {
  font-weight: 500;
}
.timeline-item__stage {
  font-size: 0.8rem;
}
.timeline-item__error {
  color: #dc2626;
  font-size: 0.8rem;
}
.timeline-more {
  margin-top: 0.6rem;
}
.dag-cols {
  display: flex;
  gap: 1.5rem;
  align-items: flex-start;
  overflow-x: auto;
}
.dag-col {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-width: 9rem;
}
.dag-node {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.5rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface, transparent);
  text-align: left;
  cursor: pointer;
}
.dag-node:hover {
  border-color: var(--color-accent, #2563eb);
}
.dag-node__type {
  font-weight: 600;
  font-size: 0.9rem;
}
.dag-node__meta {
  font-size: 0.8rem;
}
.dag-hint {
  margin-top: 0.75rem;
  font-size: 0.8rem;
}
</style>
