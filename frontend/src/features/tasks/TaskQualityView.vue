<script setup lang="ts">
// 质量工作区（D-062）：只诊断，不修改数据。指标来自后端 Quality Query API；
// 点击指标卡通过 buildDataLink 跳到 M-13 Data 页对应筛选，不在本页复制数据编辑能力。
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { buildDataLink, drilldownIsEmpty } from '@/features/data/buildDataLink'
import type { QualityDrilldown } from '@/features/quality/types'
import { useQuality } from '@/features/quality/useQuality'

const route = useRoute()
const router = useRouter()
const taskId = computed(() => String(route.params.taskId))

const { view, loading, error } = useQuality(taskId)

const isEmpty = computed(() => !!view.value && view.value.summary.total_records === 0)

function openDrilldown(item: { drilldown: QualityDrilldown }): void {
  if (drilldownIsEmpty(item.drilldown)) return
  void router.push(buildDataLink(taskId.value, item.drilldown))
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}
</script>

<template>
  <section class="task-workspace">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="view">
      <p v-if="isEmpty" class="empty">暂无质量报告</p>
      <template v-else>
        <p class="muted quality-version" data-testid="quality-version">
          指标基于数据集 {{ view.dataset_version || '—' }}
          <template v-if="view.validation_version"> · 验证 v{{ view.validation_version }}</template>
          <template v-if="view.sampling_policy_version">
            · 抽样 v{{ view.sampling_policy_version }}</template
          >
        </p>

        <div class="quality-cards">
          <button
            v-for="item in view.items"
            :key="item.key"
            type="button"
            class="quality-card"
            data-testid="quality-card"
            :class="{ 'quality-card--clickable': !drilldownIsEmpty(item.drilldown) }"
            @click="openDrilldown(item)"
          >
            <span class="quality-card__value">{{ item.value }}</span>
            <span class="quality-card__label">{{ item.label }}</span>
          </button>
        </div>

        <h3 class="quality-section">字段完整性</h3>
        <table v-if="view.field_completeness.length" class="quality-table">
          <thead>
            <tr>
              <th>字段</th>
              <th>非空</th>
              <th>缺失</th>
              <th>完成率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in view.field_completeness" :key="row.field_name">
              <td>{{ row.field_name }}</td>
              <td>{{ row.non_null }} / {{ row.total }}</td>
              <td>{{ row.missing }}</td>
              <td>{{ percent(row.completion_rate) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">无已声明字段</p>

        <h3 class="quality-section">来源覆盖</h3>
        <ul v-if="view.source_coverage.length" class="quality-sources">
          <li v-for="row in view.source_coverage" :key="row.source_type">
            <button
              type="button"
              class="quality-source"
              data-testid="quality-source"
              @click="openDrilldown({ drilldown: { source_type: row.source_type } })"
            >
              {{ row.source_type }}：{{ row.record_count }} 条记录
              <span v-if="!row.covered" class="muted">（未覆盖）</span>
            </button>
          </li>
        </ul>
        <p v-else class="muted">无来源事实</p>

        <h3 class="quality-section">抽样验证</h3>
        <p class="muted">
          {{ view.sampling.sample_count }} 条抽样
          <template v-if="view.sampling.accuracy != null">
            · 准确率 {{ percent(view.sampling.accuracy) }}</template
          >
        </p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.quality-version {
  margin-bottom: 0.75rem;
}
.quality-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr));
  gap: 0.6rem;
  margin-bottom: 1rem;
}
.quality-card {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.7rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface, transparent);
  text-align: left;
  cursor: default;
}
.quality-card--clickable {
  cursor: pointer;
}
.quality-card--clickable:hover {
  background: var(--color-surface-hover, rgba(0, 0, 0, 0.03));
}
.quality-card__value {
  font-size: 1.4rem;
  font-weight: 600;
}
.quality-card__label {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.quality-section {
  margin: 1rem 0 0.4rem;
  font-size: 0.95rem;
  font-weight: 600;
}
.quality-table {
  width: 100%;
  border-collapse: collapse;
}
.quality-table th,
.quality-table td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  font-size: 0.9rem;
}
.quality-sources {
  list-style: none;
  margin: 0;
  padding: 0;
}
.quality-source {
  border: none;
  background: none;
  color: var(--color-accent, #2563eb);
  cursor: pointer;
  padding: 0.2rem 0;
  font-size: 0.9rem;
}
</style>
