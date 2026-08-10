<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { openDrawer } from '@/app/overlay/drawer.store'
import type { TaskStatusPayload } from '@/app/overlay/drawers/TaskStatusDrawer.vue'
import { useTaskShell } from '@/features/tasks/useTaskShell'

// Task 一级工作区（D-044）。顶部只保留「对话 / 数据 / 质量」三个 Tab；
// 执行详情与证据是二级页面，不成第 4 Tab，也不进全局导航。
// owner-safe：无权/不存在 Task 渲染通用 not-found，不泄漏任何 task 元数据。
const route = useRoute()
const taskId = computed(() => (typeof route.params.taskId === 'string' ? route.params.taskId : ''))
const taskPrefix = computed(() => `/tasks/${taskId.value}`)

const { summary, loading, notFound, state } = useTaskShell(taskId)

function openStatusDrawer(): void {
  if (!summary.value) return
  const payload: TaskStatusPayload = {
    taskId: summary.value.task_id,
    title: summary.value.title,
    state: summary.value.state,
    version: summary.value.version,
    currentSpecVersion: summary.value.current_spec_version,
    currentPlanVersion: summary.value.current_plan_version,
    allowedActions: summary.value.allowed_actions,
  }
  openDrawer('TASK_STATUS', payload)
}
</script>

<template>
  <div class="task-shell">
    <section v-if="notFound" class="task-workspace">
      <p class="empty">任务不存在或无权访问</p>
    </section>
    <template v-else>
      <header class="task-shell__header">
        <span class="task-shell__title">{{ summary?.title ?? `任务 ${taskId}` }}</span>
        <span v-if="state" class="task-shell__state">{{ state }}</span>
        <button
          v-if="summary"
          type="button"
          class="task-shell__status"
          @click="openStatusDrawer"
        >
          状态
        </button>
        <span v-if="loading" class="muted">加载中…</span>
      </header>

      <nav class="task-shell__tabs" aria-label="任务工作区">
        <RouterLink :to="`${taskPrefix}/chat`" class="task-shell__tab">对话</RouterLink>
        <RouterLink :to="`${taskPrefix}/data`" class="task-shell__tab">数据</RouterLink>
        <RouterLink :to="`${taskPrefix}/quality`" class="task-shell__tab">质量</RouterLink>
      </nav>

      <div class="task-shell__body">
        <RouterView />
      </div>
    </template>
  </div>
</template>

<style scoped>
.task-shell__header {
  display: flex;
  align-items: baseline;
  gap: 1rem;
  margin-bottom: 0.75rem;
}
.task-shell__title {
  font-weight: 600;
  font-size: 1.1rem;
}
.task-shell__state {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  padding: 0.1rem 0.6rem;
}
.task-shell__status {
  padding: 0.15rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.task-shell__tabs {
  display: flex;
  gap: 0.5rem;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 1rem;
}
.task-shell__tab {
  padding: 0.5rem 1rem;
  color: var(--color-text-secondary);
  text-decoration: none;
  border-bottom: 2px solid transparent;
}
.task-shell__tab:hover {
  color: var(--color-text);
}
.task-shell__tab.router-link-exact-active {
  border-bottom-color: var(--color-text);
  color: var(--color-text);
  font-weight: 600;
}
.task-shell__body {
  min-height: 40vh;
}
</style>
