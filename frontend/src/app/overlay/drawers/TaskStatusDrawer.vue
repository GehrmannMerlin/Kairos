<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listPendingTaskApprovals } from '@/features/tasks/approvals.api'
import { cancelTask, pauseTask, resumeTask } from '@/features/tasks/commands.api'
import type { TaskSseEvent } from '@/features/tasks/events.api'
import { useTaskShell } from '@/features/tasks/useTaskShell'
import { useTaskEvents } from '@/features/tasks/useTaskEvents'

export interface TaskStatusPayload {
  taskId: number | string
}

const props = defineProps<{ payload?: unknown }>()
const payload = props.payload as TaskStatusPayload | undefined
const taskIdRef = computed(() => String(payload?.taskId ?? ''))
const taskId = ref(taskIdRef.value)
watch(taskIdRef, (v) => {
  taskId.value = v
})

const { summary, loading, can, load } = useTaskShell(taskId)
const { connection, latestEvent, connect, disconnect } = useTaskEvents(taskId)

const busy = ref(false)
const notice = ref('')
const pendingApprovals = ref(0)

async function loadPendingApprovals(): Promise<void> {
  if (!taskId.value) return
  try {
    const data = await listPendingTaskApprovals(taskId.value)
    pendingApprovals.value = data.approvals.length
  } catch {
    pendingApprovals.value = 0
  }
}

// 审批或任务状态变化后刷新待审批计数
watch(latestEvent, () => void loadPendingApprovals())

const connectionLabel = computed(() => {
  const map: Record<string, string> = {
    connecting: '连接中…',
    open: '实时',
    reconnecting: '重连中…',
    closed: '已断开',
    idle: '未连接',
  }
  return map[connection.value] ?? connection.value
})

async function runCommand(cmd: 'pause' | 'resume' | 'cancel'): Promise<void> {
  if (!can(cmd) || busy.value) return
  const version = summary.value?.version
  if (version === undefined) return
  busy.value = true
  notice.value = ''
  try {
    const fn = { pause: pauseTask, resume: resumeTask, cancel: cancelTask }[cmd]
    await fn(taskId.value, { expectedVersion: version })
    await load() // 立即拉取真实状态（PAUSING/CANCELLING 中间态来自后端事实）
  } catch (err) {
    notice.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

// SSE 断线自动重连后，后端事件流已续上，但 Task Snapshot 可能是断线期间过期的
// 旧状态（useTaskEvents 契约：恢复后由调用方重新拉取）。检测 reconnecting→open
// 转换（跳过初次 connecting→open，初次由 onMounted load() 覆盖）时重新拉取。
watch(connection, (next, prev) => {
  if (next === 'open' && prev === 'reconnecting') {
    void load()
  }
})

onMounted(() => {
  void load()
  void loadPendingApprovals()
  connect()
})
onBeforeUnmount(disconnect)

const importantEvent = computed<TaskSseEvent | null>(() => latestEvent.value)
</script>

<template>
  <div v-if="summary" class="status-drawer">
    <dl class="status-list">
      <div class="status-row">
        <dt>任务</dt>
        <dd>{{ summary.title }}</dd>
      </div>
      <div class="status-row">
        <dt>状态</dt>
        <dd>{{ summary.state }}</dd>
      </div>
      <div class="status-row">
        <dt>Spec 版本</dt>
        <dd>{{ summary.current_spec_version ?? '—' }}</dd>
      </div>
      <div class="status-row">
        <dt>Plan 版本</dt>
        <dd>{{ summary.current_plan_version ?? '—' }}</dd>
      </div>
      <div class="status-row">
        <dt>待审批</dt>
        <dd data-test="pending-approvals">{{ pendingApprovals }}</dd>
      </div>
      <div class="status-row">
        <dt>事件流</dt>
        <dd>{{ connectionLabel }}</dd>
      </div>
    </dl>

    <p v-if="importantEvent" class="muted">最近事件：{{ importantEvent.event_type }}</p>

    <div class="command-row">
      <button
        type="button"
        class="ghost"
        :disabled="!can('pause') || busy"
        @click="runCommand('pause')"
      >
        暂停
      </button>
      <button
        type="button"
        class="ghost"
        :disabled="!can('resume') || busy"
        @click="runCommand('resume')"
      >
        恢复
      </button>
      <button
        type="button"
        class="danger"
        :disabled="!can('cancel') || busy"
        @click="runCommand('cancel')"
      >
        取消
      </button>
    </div>
    <p v-if="notice" class="error">{{ notice }}</p>
    <p v-if="loading" class="muted">加载中…</p>
  </div>
  <p v-else class="muted">任务状态信息暂不可用</p>
</template>

<style scoped>
.status-list {
  margin: 0 0 1.25rem;
}
.status-row {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px dashed var(--color-border);
}
.status-row dt {
  color: var(--color-text-secondary);
}
.status-row dd {
  margin: 0;
  word-break: break-word;
}
.command-row {
  display: flex;
  gap: 0.5rem;
  margin-top: 1rem;
}
.muted {
  color: var(--color-text-secondary);
  font-size: 0.85rem;
}
.error {
  color: var(--color-danger, #c0392b);
  font-size: 0.85rem;
}
</style>
