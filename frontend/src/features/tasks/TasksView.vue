<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { listTasks, type TaskShellDto } from '@/features/tasks/tasks.api'

// 我的任务（D-046）。真实 owner-safe 列表读取。
// `?view=needs_action`（D-049）复用同一列表查询，按真实后端状态聚合待处理
// （WAITING_APPROVAL / WAITING_RESOURCE 等）；不新增 Inbox 页面。
const route = useRoute()
const view = computed(() => route.query.view)

const tasks = ref<TaskShellDto[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const NEEDS_ACTION_STATES = new Set(['WAITING_APPROVAL', 'WAITING_RESOURCE'])

const visibleTasks = computed(() => {
  if (view.value !== 'needs_action') return tasks.value
  return tasks.value.filter((t) => NEEDS_ACTION_STATES.has(t.state))
})

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    tasks.value = (await listTasks()).tasks
  } catch {
    error.value = '加载任务列表失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>{{ view === 'needs_action' ? '待处理' : '我的任务' }}</h1>
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
        </tr>
      </tbody>
    </table>
    <p v-else-if="!loading" class="empty">
      {{ view === 'needs_action' ? '暂无待处理任务' : '暂无任务' }}
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
</style>
