<script setup lang="ts">
import { onMounted } from 'vue'

import { fetchHealthLive, fetchHealthReady } from '@/app/api/health.api'
import { useAsync } from '@/app/composables/useAsync'

const {
  status: liveStatus,
  data: liveData,
  error: liveError,
  run: runLive,
} = useAsync(fetchHealthLive)
const {
  status: readyStatus,
  data: readyData,
  error: readyError,
  run: runReady,
} = useAsync(fetchHealthReady)

function checkNames(): string[] {
  return Object.keys(readyData.value?.checks ?? {})
}

function checkStatus(name: string): string {
  return readyData.value?.checks[name]?.status ?? 'unknown'
}

onMounted(() => {
  void runLive()
  void runReady()
})
</script>

<template>
  <section class="home">
    <h1 class="home__title">Kairos 工程骨架</h1>
    <p class="home__subtitle">M-01 基础闭环 — 前端 → API → 基础设施连通性检查</p>

    <div class="card" data-testid="health-live">
      <h2>API 存活 (/api/health/live)</h2>
      <template v-if="liveStatus === 'loading'">
        <p class="muted">检查中…</p>
      </template>
      <template v-else-if="liveStatus === 'error'">
        <p class="error">无法连接 API:{{ liveError }}</p>
      </template>
      <template v-else-if="liveStatus === 'success'">
        <p class="ok">ok · {{ liveData?.service }}</p>
      </template>
    </div>

    <div class="card" data-testid="health-ready">
      <h2>基础设施就绪 (/api/health/ready)</h2>
      <template v-if="readyStatus === 'loading'">
        <p class="muted">检查中…</p>
      </template>
      <template v-else-if="readyStatus === 'error'">
        <p class="error">无法连接 API:{{ readyError }}</p>
      </template>
      <template v-else-if="readyStatus === 'success'">
        <p class="ok">status = {{ readyData?.status }}</p>
        <ul class="checks">
          <li v-for="name in checkNames()" :key="name">
            <span class="check-name">{{ name }}</span>
            <span :class="['check-status', checkStatus(name)]">{{ checkStatus(name) }}</span>
          </li>
        </ul>
      </template>
    </div>
  </section>
</template>

<style scoped>
.home__title {
  font-size: 1.4rem;
  margin: 0 0 0.25rem;
}
.home__subtitle {
  color: var(--color-text-secondary);
  margin: 0 0 1.5rem;
}
.card {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
  max-width: 560px;
}
.card h2 {
  font-size: 0.95rem;
  margin: 0 0 0.5rem;
}
.ok {
  color: var(--color-success);
}
.error {
  color: var(--color-danger);
}
.muted {
  color: var(--color-text-secondary);
}
.checks {
  list-style: none;
  margin: 0.5rem 0 0;
  padding: 0;
}
.checks li {
  display: flex;
  justify-content: space-between;
  padding: 0.25rem 0;
  border-top: 1px dashed var(--color-border);
}
.check-status.ok {
  color: var(--color-success);
}
.check-status.error {
  color: var(--color-danger);
}
</style>
