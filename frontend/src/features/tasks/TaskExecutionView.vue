<script setup lang="ts">
// 执行详情二级页（D-055/D-063）：默认"阶段 + 时间线"，可切换只读 Plan DAG。
// 阶段/时间线全部来自后端 Execution/Timeline Query；Token 只作技术统计，无金额 UI。
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'

import { openDrawer } from '@/app/overlay/drawer.store'
import TimelineStepRow from '@/features/execution/TimelineStepRow.vue'
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
  live,
  loadMore,
  setFilter,
  toggleDag,
  connectLive,
  disconnectLive,
} = useExecution(taskId)

// ---- Timeline 流生命周期：run 非终态时连接，进入终态断开，卸载不泄漏 ----
const TERMINAL_RUN_STATES = new Set([
  'COMPLETED',
  'PARTIALLY_COMPLETED',
  'FAILED',
  'CANCELLED',
  'BLOCKED',
])

// 监听 taskId 与 run state：跨任务导航时即便两任务均为相同激活状态（如 RUNNING→RUNNING），
// 也要在 hook 已断开旧流后重新连接新任务流；连接判定以实际 live 连接状态为准。
watch(
  [taskId, () => view.value?.run?.state],
  ([, state]) => {
    if (state == null) return
    const terminal = TERMINAL_RUN_STATES.has(state)
    if (terminal) {
      disconnectLive()
    } else if (live.value === 'idle') {
      connectLive()
    }
  },
  { immediate: true },
)

// 终态 / 无 run 的 live 徽标状态：终态显示"已结束"，无 run 不显示徽标。
const runEnded = computed(() => {
  const state = view.value?.run?.state ?? null
  return state != null && TERMINAL_RUN_STATES.has(state)
})

onBeforeUnmount(disconnectLive)

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

// ---- DAG 节点 live 着色：由 node.execution.last_status 驱动，未知/null 用中性样式 ----
function dagNodeStatusClass(node: DagNode): string {
  const status = node.execution.last_status ?? ''
  if (status === 'SUCCEEDED') return 'dag-node--succeeded'
  if (status === 'FAILED') return 'dag-node--failed'
  if (status === 'RUNNING' || status === 'PENDING') return 'dag-node--running'
  if (status.startsWith('WAITING_')) return 'dag-node--waiting'
  return 'dag-node--idle'
}
</script>

<template>
  <section class="task-workspace">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="view">
      <div class="exec-header">
        <div class="exec-header__row">
          <p class="muted">
            运行状态：{{ view.run?.state ?? '—' }}
            <template v-if="view.run">
              · Run #{{ view.run.run_id }} · 计划 v{{ view.plan?.plan_version }}</template
            >
          </p>
          <span v-if="runEnded" class="live-badge live-badge--ended">已结束</span>
          <span
            v-else-if="view.run"
            class="live-badge"
            :class="{
              'live-badge--open': live === 'open',
              'live-badge--reconnecting': live === 'reconnecting',
            }"
          >
            <span v-if="live === 'open'" class="live-badge__dot" />
            {{
              live === 'reconnecting'
                ? '连接中断，正在恢复…'
                : live === 'open'
                  ? '实时更新中'
                  : '实时更新'
            }}
          </span>
        </div>
        <p class="muted">
          URL：发现 {{ view.urls.discovered ?? 0 }} / 抓取 {{ view.urls.fetched ?? 0 }} / 失败
          {{ view.urls.failed ?? 0 }}
          <template v-if="view.records">
            · 记录：通过 {{ view.records.passed ?? 0 }} / 待复核
            {{ view.records.needs_review ?? 0 }}</template
          >
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
              事件 {{ stage.event_count
              }}<template v-if="stage.url_processed"> · URL {{ stage.url_processed }}</template>
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
          <TimelineStepRow
            v-for="ev in timeline"
            :key="ev.event_id"
            :event="ev"
            :active="ev.node_id === view?.current_node?.node_id"
          />
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
                  :class="[
                    dagNodeStatusClass(node),
                    { 'dag-node--active': node.node_id === view?.current_node?.node_id },
                  ]"
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
  display: grid;
  gap: 0.3rem;
  margin-bottom: 0.75rem;
}
.exec-header__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}
.exec-header__row .muted {
  margin: 0;
}
.live-badge {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  color: var(--color-text-secondary);
  font-size: 0.78rem;
}
.live-badge__dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: var(--color-success, #1a7f37);
}
.live-badge--reconnecting {
  color: var(--color-danger, #c62828);
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
  animation: stage-card-pulse 1.6s ease-in-out infinite;
}
@keyframes stage-card-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.35);
  }
  50% {
    box-shadow: 0 0 0 5px rgba(37, 99, 235, 0);
  }
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
.dag-node--succeeded {
  border-color: #16a34a;
}
.dag-node--failed {
  border-color: #dc2626;
}
.dag-node--running {
  border-color: var(--color-accent, #2563eb);
  animation: dag-node-pulse 1.2s ease-in-out infinite;
}
.dag-node--waiting {
  border-color: #b45309;
}
.dag-node--active {
  outline: 2px solid var(--color-accent, #2563eb);
  outline-offset: 1px;
}
@keyframes dag-node-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.6;
  }
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
