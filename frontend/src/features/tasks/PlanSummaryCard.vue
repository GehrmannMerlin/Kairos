<script setup lang="ts">
import { computed } from 'vue'

import type { PlanSummaryDto } from '@/features/tasks/plans.api'

// D-025/D-038：Plan 可查看；合法低风险 Plan 自动执行，无二次「确认 Plan」按钮。
// 不显示任何金额/费用字段（D-036）。
const props = defineProps<{ summary: PlanSummaryDto }>()

const riskLabel = computed(() => {
  const map: Record<string, string> = {
    VALID: '低风险，自动执行',
    REQUIRES_APPROVAL: '含高风险步骤，执行时将请求审批',
    REQUIRES_NEW_SPEC: '需要新的采集方案版本',
    INVALID: '计划不合法',
    PROHIBITED: '包含禁止动作',
  }
  return map[props.summary.validation_status] ?? props.summary.validation_status
})
</script>

<template>
  <section class="plan-summary" data-test="plan-summary">
    <header class="plan-summary__head">
      <span class="plan-summary__title">执行计划</span>
      <span class="plan-summary__version">v{{ summary.plan_version }}</span>
    </header>
    <dl class="plan-summary__grid">
      <div class="row">
        <dt>校验结果</dt>
        <dd>{{ summary.validation_status }}</dd>
      </div>
      <div class="row">
        <dt>风险</dt>
        <dd>{{ riskLabel }}</dd>
      </div>
      <div class="row">
        <dt>节点数</dt>
        <dd>{{ summary.node_count }}</dd>
      </div>
      <div class="row">
        <dt>主要步骤</dt>
        <dd>{{ summary.node_types.filter(Boolean).join(' → ') || '—' }}</dd>
      </div>
    </dl>
    <!-- D-038：合法低风险 Plan 不提供「确认 Plan」按钮 -->
  </section>
</template>

<style scoped>
.plan-summary {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 0.75rem 1rem;
  background: var(--color-surface);
}
.plan-summary__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.plan-summary__title {
  font-weight: 600;
  font-size: 0.95rem;
}
.plan-summary__version {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}
.plan-summary__grid {
  margin: 0;
}
.row {
  display: flex;
  gap: 1rem;
  padding: 0.25rem 0;
}
.row dt {
  width: 5rem;
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  flex: none;
}
.row dd {
  margin: 0;
  font-size: 0.85rem;
  word-break: break-word;
}
</style>
