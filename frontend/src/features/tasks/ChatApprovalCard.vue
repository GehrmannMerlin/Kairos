<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { openDrawer } from '@/app/overlay/drawer.store'
import { getApproval, type ApprovalDto } from '@/features/tasks/approvals.api'

// Chat 时间线内审批卡（D-039/D-042）。引用真实 approval_id；
// 点击打开同一个 Approval Drawer（不创建第二份前端 Approval object）。
const props = defineProps<{ approvalId: number | string }>()

const approval = ref<ApprovalDto | null>(null)
const failed = ref(false)

async function load(): Promise<void> {
  try {
    approval.value = await getApproval(props.approvalId)
  } catch {
    failed.value = true
  }
}

function openDrawerForApproval(): void {
  openDrawer('APPROVAL', { approvalId: props.approvalId })
}

onMounted(() => void load())
</script>

<template>
  <button type="button" class="approval-card" @click="openDrawerForApproval">
    <span class="approval-card__title">需要审批</span>
    <span v-if="approval" class="approval-card__action">{{ approval.action_type }}</span>
    <span v-if="approval?.target" class="approval-card__target">{{ approval.target }}</span>
    <span v-if="approval" class="approval-card__state">{{ approval.state }}</span>
    <span v-else-if="failed" class="approval-card__failed">审批不可用</span>
  </button>
</template>

<style scoped>
.approval-card {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  text-align: left;
  width: 100%;
  padding: 0.6rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-surface);
  cursor: pointer;
  font: inherit;
  color: var(--color-text);
}
.approval-card:hover {
  border-color: var(--color-text-secondary);
}
.approval-card__title {
  font-weight: 600;
  font-size: 0.9rem;
}
.approval-card__action,
.approval-card__target,
.approval-card__state {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
.approval-card__state {
  color: var(--color-warning, #b8860b);
}
.approval-card__failed {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
</style>
