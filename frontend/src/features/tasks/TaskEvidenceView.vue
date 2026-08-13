<script setup lang="ts">
// 证据查看器二级页（D-056/D-064）。展示任务当时保存的快照，绝不重新请求当前网页。
// 优先级：视觉快照(Snapshot) > 提取正文(Text) > 原始内容(Raw，sandbox iframe)。
// 全部只读；locator 在已加载快照内定位，失败显示 fallback。
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useEvidence } from '@/features/evidence/useEvidence'
import type { EvidenceFieldEvidence } from '@/features/evidence/types'

const route = useRoute()
const taskId = computed(() => String(route.params.taskId))
const snapshotId = computed(() =>
  typeof route.params.evidenceId === 'string' ? route.params.evidenceId : '',
)

const { view, loading, error, content, contentLoading, locateResult, sandboxHtml, searchQuery, locate } =
  useEvidence(taskId, snapshotId)

const MODE_LABEL = { snapshot: '视觉快照', text: '提取正文', raw: '原始内容' } as const

const filteredEvidence = computed<EvidenceFieldEvidence[]>(() => {
  if (!view.value) return []
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return view.value.field_evidence
  return view.value.field_evidence.filter(
    (ev) =>
      (ev.field_name ?? '').toLowerCase().includes(q) ||
      (ev.value ?? '').toLowerCase().includes(q),
  )
})

const textForSearch = computed(() => {
  if (!view.value || view.value.display_mode === 'snapshot') return ''
  return content.value && !content.value.isImage ? content.value.text : view.value.summary ?? ''
})

const searchCount = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q || !textForSearch.value) return 0
  return textForSearch.value.toLowerCase().split(q).length - 1
})

function openSource(): void {
  if (view.value?.source_url) window.open(view.value.source_url, '_blank', 'noopener')
}

function downloadEvidence(): void {
  if (!view.value) return
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api'
  window.open(`${base}${view.value.download_url}?download=1`, '_blank')
}
</script>

<template>
  <section class="task-workspace">
    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <template v-else-if="view">
      <div class="evidence-meta">
        <p class="muted">
          来源：<a v-if="view.source_url" href="#" class="evidence-link" @click.prevent="openSource">{{ view.source_url }}</a>
          <span v-else>—</span>
        </p>
        <p class="muted">
          抓取时间：{{ view.fetched_at ? new Date(view.fetched_at).toLocaleString() : '—' }} ·
          快照 v{{ view.snapshot_version }} · {{ view.tool }} v{{ view.tool_version }}
        </p>
        <p class="muted">
          展示模式：<span class="evidence-mode">{{ MODE_LABEL[view.display_mode] }}</span>
          <template v-if="view.mime_type"> · {{ view.mime_type }}</template>
          <template v-if="view.http_status != null"> · HTTP {{ view.http_status }}</template>
        </p>
        <p class="evidence-actions">
          <button v-if="view.display_mode !== 'snapshot' && content && !content.isImage" type="button" class="evidence-btn" @click="locate">
            定位到页面位置
          </button>
          <button v-if="view.has_content" type="button" class="evidence-btn" @click="downloadEvidence">
            下载证据
          </button>
          <button v-if="view.source_url" type="button" class="evidence-btn" @click="openSource">
            打开原始来源
          </button>
        </p>
      </div>

      <div v-if="locateResult" class="evidence-locate" data-testid="evidence-locate">
        <span v-if="locateResult.found" class="evidence-locate--ok">已定位：{{ locateResult.snippet }}</span>
        <span v-else class="evidence-locate--fail">无法定位，仍展示原始证据片段</span>
      </div>

      <div class="evidence-body">
        <img v-if="view.display_mode === 'snapshot' && content?.isImage" :src="content.imageUrl" class="evidence-image" alt="页面快照" />
        <template v-else-if="view.display_mode === 'text'">
          <p v-if="contentLoading" class="muted">加载正文…</p>
          <pre v-else class="evidence-text">{{ view.summary || (content && content.text) || '（无正文）' }}</pre>
        </template>
        <template v-else-if="view.display_mode === 'raw'">
          <p v-if="contentLoading" class="muted">加载原始内容…</p>
          <iframe
            v-else-if="sandboxHtml"
            class="evidence-frame"
            sandbox=""
            :srcdoc="sandboxHtml"
            title="历史快照（沙箱只读）"
          ></iframe>
          <p v-else class="muted">该证据无已保存内容</p>
        </template>
        <p v-else-if="!content" class="muted">该证据无视觉快照</p>
      </div>

      <div class="evidence-toolbar">
        <input v-model="searchQuery" type="search" class="evidence-search" placeholder="在当前内容中搜索" />
        <span v-if="searchQuery.trim() && searchCount > 0" class="muted">匹配 {{ searchCount }} 处</span>
      </div>

      <h3 class="evidence-section">字段证据</h3>
      <table v-if="filteredEvidence.length" class="evidence-table">
        <thead>
          <tr>
            <th>字段</th>
            <th>值</th>
            <th>片段</th>
            <th>提取方式</th>
            <th>置信度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ev in filteredEvidence" :key="`${ev.record_id}-${ev.field_name}`">
            <td>{{ ev.field_name }}</td>
            <td>{{ ev.value || '—' }}</td>
            <td class="evidence-cell--muted">{{ ev.raw_snippet || '—' }}</td>
            <td class="evidence-cell--muted">
              {{ ev.extract_method }} <template v-if="ev.extractor_version">v{{ ev.extractor_version }}</template>
            </td>
            <td class="evidence-cell--muted">{{ ev.confidence ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="searchQuery.trim()" class="muted">无匹配字段证据</p>
      <p v-else class="muted">无字段证据</p>
    </template>
  </section>
</template>

<style scoped>
.evidence-meta {
  margin-bottom: 0.75rem;
}
.evidence-mode {
  font-weight: 600;
}
.evidence-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.evidence-btn {
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-secondary);
}
.evidence-link {
  color: var(--color-accent, #2563eb);
  word-break: break-all;
}
.evidence-locate {
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  margin-bottom: 0.6rem;
  font-size: 0.85rem;
}
.evidence-locate--ok {
  color: var(--color-accent, #2563eb);
}
.evidence-locate--fail {
  color: #b45309;
}
.evidence-body {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.6rem;
  margin-bottom: 0.6rem;
  min-height: 6rem;
}
.evidence-image {
  max-width: 100%;
  border-radius: 6px;
}
.evidence-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.85rem;
  margin: 0;
  max-height: 24rem;
  overflow: auto;
}
.evidence-frame {
  width: 100%;
  height: 30rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: #fff;
}
.evidence-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.evidence-search {
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
}
.evidence-section {
  margin: 1rem 0 0.4rem;
  font-size: 0.95rem;
  font-weight: 600;
}
.evidence-table {
  width: 100%;
  border-collapse: collapse;
}
.evidence-table th,
.evidence-table td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  font-size: 0.85rem;
  vertical-align: top;
}
.evidence-cell--muted {
  color: var(--color-text-secondary);
}
</style>
