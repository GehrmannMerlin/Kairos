<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { openModal } from '@/app/overlay/modal.store'
import { restoreTask } from '@/features/tasks/commands.api'
import { listTasks, type TaskShellDto } from '@/features/tasks/tasks.api'

// 我的任务（D-046）+ 已删除视图（D-065，/tasks?view=deleted）。
// `?view=needs_action`（D-049）复用同一列表查询，按真实后端状态聚合待处理。
const route = useRoute()
const view = computed(() => route.query.view)

const tasks = ref<TaskShellDto[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const NEEDS_ACTION_STATES = new Set(['WAITING_APPROVAL', 'WAITING_RESOURCE'])

const visibleTasks = computed(() => {
  if (view.value === 'deleted') return tasks.value
  if (view.value !== 'needs_action') return tasks.value
  return tasks.value.filter((t) => NEEDS_ACTION_STATES.has(t.state))
})

function can(t: TaskShellDto, action: string): boolean {
  return t.allowed_actions.includes(action)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    tasks.value = (await listTasks({ view: view.value as string | undefined })).tasks
  } catch {
    error.value = '加载任务列表失败'
  } finally {
    loading.value = false
  }
}

function onSoftDelete(t: TaskShellDto): void {
  openModal('DELETE_CONFIRM', {
    taskId: t.task_id,
    version: t.version,
    action: 'soft',
    onDone: () => void load(),
  })
}

function onPermanentDelete(t: TaskShellDto): void {
  openModal('DELETE_CONFIRM', {
    taskId: t.task_id,
    version: t.version,
    action: 'permanent',
    onDone: () => void load(),
  })
}

async function onRestore(t: TaskShellDto): Promise<void> {
  try {
    await restoreTask(t.task_id, { expectedVersion: t.version })
    void load()
  } catch {
    error.value = '恢复失败'
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>{{ view === 'needs_action' ? '待处理' : view === 'deleted' ? '已删除' : '我的任务' }}</h1>
    </header>

    <div class="toolbar">
      <span class="muted">搜索 / 筛选 / 排序将在后续模块接入</span>
    </div>

    <p v-if="error" class="form-error">{{ error }}</p>
    <p v-if="loading" class="muted">加载中…</p>

    <table v-if="visibleTasks.length" class="task-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>状态</th>
          <th>最近活动</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in visibleTasks" :key="t.task_id">
          <td>
            <RouterLink :to="`/tasks/${t.task_id}/chat`" class="task-link">{{
              t.title
            }}</RouterLink>
          </td>
          <td>{{ t.state }}</td>
          <td class="muted">{{ t.updated_at }}</td>
          <td class="task-actions">
            <template v-if="view === 'deleted'">
              <button
                v-if="can(t, 'restore')"
                type="button"
                class="task-btn"
                data-testid="restore"
                @click="onRestore(t)"
              >
                恢复
              </button>
              <button
                type="button"
                class="task-btn task-btn--danger"
                data-testid="permanent-delete"
                @click="onPermanentDelete(t)"
              >
                永久删除
              </button>
            </template>
            <button
              v-else-if="can(t, 'delete')"
              type="button"
              class="task-btn task-btn--danger"
              data-testid="soft-delete"
              @click="onSoftDelete(t)"
            >
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="!loading" class="empty">
      {{ view === 'needs_action' ? '暂无待处理任务' : view === 'deleted' ? '暂无已删除任务' : '暂无任务' }}
    </p>
  </section>
</template>

<style scoped>
.task-table {
  width: 100%;
  border-collapse: collapse;
}
.task-table th,
.task-table td {
  border: 1px solid var(--color-border);
  padding: 0.5rem;
  text-align: left;
  font-size: 0.9rem;
}
.task-link {
  color: var(--color-text);
  text-decoration: none;
}
.task-link:hover {
  text-decoration: underline;
}
.task-actions {
  display: flex;
  gap: 0.4rem;
}
.task-btn {
  padding: 0.25rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.8rem;
}
.task-btn--danger {
  color: #c62828;
  border-color: #c62828;
}
</style>
