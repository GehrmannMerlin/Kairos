<script setup lang="ts">
// Task Status Drawer（D-032/D-067）。展示后端真实事实；不存在的计数显示「—」，
// 不写静态数字；command 未实现的 action 只作信息展示，不提供可点击假按钮。
export interface TaskStatusPayload {
  taskId: number | string
  title: string
  state: string
  version: number
  currentSpecVersion?: number | null
  currentPlanVersion?: number | null
  allowedActions: string[]
}

const props = defineProps<{ payload?: unknown }>()
const data = props.payload as TaskStatusPayload | undefined
</script>

<template>
  <template v-if="data">
    <dl class="status-list">
      <div class="status-row"><dt>任务</dt><dd>{{ data.title }}</dd></div>
      <div class="status-row"><dt>ID</dt><dd>{{ data.taskId }}</dd></div>
      <div class="status-row"><dt>状态</dt><dd>{{ data.state }}</dd></div>
      <div class="status-row"><dt>版本</dt><dd>{{ data.version }}</dd></div>
      <div class="status-row">
        <dt>Spec 版本</dt>
        <dd>{{ data.currentSpecVersion ?? '—' }}</dd>
      </div>
      <div class="status-row">
        <dt>Plan 版本</dt>
        <dd>{{ data.currentPlanVersion ?? '—' }}</dd>
      </div>
    </dl>

    <section>
      <h3 class="status-actions__title">可执行动作</h3>
      <p class="muted">对应命令将在各模块接入后开放</p>
      <div class="status-actions__tags">
        <span v-for="a in data.allowedActions" :key="a" class="status-tag">{{ a }}</span>
      </div>
    </section>
  </template>
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
.status-actions__title {
  font-size: 0.95rem;
  margin: 0 0 0.25rem;
}
.status-actions__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.5rem;
}
.status-tag {
  border: 1px solid var(--color-border);
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
</style>
