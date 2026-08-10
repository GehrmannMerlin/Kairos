<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { listTasks, type TaskShellDto } from '@/features/tasks/tasks.api'

// 工作台（D-045）。「+ 新任务」真实创建在 M-06 接入；M-05 只呈现占位输入。
// 最近任务读取真实 owner-safe 列表，无则真实 Empty State。
const recent = ref<TaskShellDto[]>([])
const loaded = ref(false)

async function loadRecent(): Promise<void> {
  try {
    recent.value = (await listTasks()).tasks.slice(0, 5)
  } catch {
    // 列表接口暂不可用时保持 Empty State
  } finally {
    loaded.value = true
  }
}

onMounted(() => void loadRecent())
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>工作台</h1>
    </header>

    <div class="card workbench__new-task">
      <textarea
        class="workbench__input"
        rows="3"
        disabled
        placeholder="描述你的采集需求，例如：帮我搜集深圳的工业自动化设备供应商，获取公司名、官网、主营产品和联系方式"
      />
      <p class="muted">任务创建能力将在下一模块接入（M-06）</p>
    </div>

    <div class="workbench__section">
      <h2 class="workbench__section-title">最近任务</h2>
      <ul v-if="recent.length" class="workbench__recent">
        <li v-for="t in recent" :key="t.task_id">
          <RouterLink :to="`/tasks/${t.task_id}/chat`" class="task-link">{{ t.title }}</RouterLink>
          <span class="muted">{{ t.state }}</span>
        </li>
      </ul>
      <p v-else class="empty">暂无任务</p>
    </div>
  </section>
</template>

<style scoped>
.workbench__input {
  display: block;
  width: 100%;
  padding: 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  resize: vertical;
  background: var(--color-bg);
  color: var(--color-text-secondary);
}
.workbench__section-title {
  font-size: 1rem;
  margin: 0 0 0.75rem;
}
.workbench__recent {
  list-style: none;
  margin: 0;
  padding: 0;
}
.workbench__recent li {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px dashed var(--color-border);
}
.task-link {
  color: var(--color-text);
  text-decoration: none;
}
.task-link:hover {
  text-decoration: underline;
}
</style>
