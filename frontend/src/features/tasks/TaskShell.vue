<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

// Task 一级工作区（D-044）。顶部只保留「对话 / 数据 / 质量」三个 Tab；
// 执行详情与证据是二级页面，不成第 4 Tab，也不进全局导航。
const route = useRoute()
const taskId = computed(() => (typeof route.params.taskId === 'string' ? route.params.taskId : ''))
const taskPrefix = computed(() => `/tasks/${taskId.value}`)
</script>

<template>
  <div class="task-shell">
    <header class="task-shell__header">
      <span class="task-shell__title">任务 {{ taskId }}</span>
      <span class="muted">任务状态将在后续接入</span>
    </header>

    <nav class="task-shell__tabs" aria-label="任务工作区">
      <RouterLink :to="`${taskPrefix}/chat`" class="task-shell__tab">对话</RouterLink>
      <RouterLink :to="`${taskPrefix}/data`" class="task-shell__tab">数据</RouterLink>
      <RouterLink :to="`${taskPrefix}/quality`" class="task-shell__tab">质量</RouterLink>
    </nav>

    <div class="task-shell__body">
      <RouterView />
    </div>
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
