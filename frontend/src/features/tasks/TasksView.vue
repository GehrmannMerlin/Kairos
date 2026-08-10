<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { listTasks, type TaskShellDto } from '@/features/tasks/tasks.api'

// 我的任务（D-046）。真实 owner-safe 列表读取；搜索/筛选/排序后续模块接入。
const tasks = ref<TaskShellDto[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

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
      <h1>我的任务</h1>
    </header>

    <div class="toolbar">
      <span class="muted">搜索 / 筛选 / 排序将在后续模块接入</span>
    </div>

    <p v-if="error" class="form-error">{{ error }}</p>
    <p v-if="loading" class="muted">加载中…</p>

    <table v-if="tasks.length" class="task-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>状态</th>
          <th>最近活动</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in tasks" :key="t.task_id">
          <td>
            <RouterLink :to="`/tasks/${t.task_id}/chat`" class="task-link">{{ t.title }}</RouterLink>
          </td>
          <td>{{ t.state }}</td>
          <td class="muted">{{ t.updated_at }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="!loading" class="empty">暂无任务</p>
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
