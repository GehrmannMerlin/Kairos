/** M-14 Evidence Viewer 状态（D-056/D-064）。明确 idle/loading/success/empty/error。 */

import { computed, ref, watch, type Ref } from 'vue'

import { fetchEvidenceContent, getEvidence } from './evidence.api'
import { findInSnapshotHtml } from './locator'
import { buildSandboxHtml } from './sandbox'
import type { EvidenceContent, EvidenceView, LocateResult } from './types'

export interface UseEvidence {
  view: Ref<EvidenceView | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  content: Ref<EvidenceContent | null>
  contentLoading: Ref<boolean>
  locateResult: Ref<LocateResult | null>
  sandboxHtml: Ref<string>
  searchQuery: Ref<string>
  reload: () => Promise<void>
  locate: () => void
}

export function useEvidence(
  taskId: Ref<string | number>,
  snapshotId: Ref<string | number>,
): UseEvidence {
  const view = ref<EvidenceView | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const content = ref<EvidenceContent | null>(null)
  const contentLoading = ref(false)
  const locateResult = ref<LocateResult | null>(null)
  const searchQuery = ref('')
  let seq = 0

  const sandboxHtml = computed(() =>
    view.value?.display_mode !== 'snapshot' && content.value && !content.value.isImage
      ? buildSandboxHtml(content.value.text)
      : '',
  )

  async function loadContent(): Promise<void> {
    if (!view.value || !view.value.has_content) return
    contentLoading.value = true
    try {
      content.value = await fetchEvidenceContent(view.value.download_url)
    } catch (err) {
      // 内容加载失败不影响元数据展示
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      contentLoading.value = false
    }
  }

  async function reload(): Promise<void> {
    const current = ++seq
    loading.value = true
    error.value = null
    locateResult.value = null
    try {
      view.value = await getEvidence(taskId.value, snapshotId.value)
      if (current !== seq) return
      await loadContent()
    } catch (err) {
      if (current !== seq) return
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      if (current === seq) loading.value = false
    }
  }

  function locate(): void {
    if (!view.value) return
    const locator = view.value.field_evidence[0]?.source_locator ?? null
    if (view.value.display_mode === 'snapshot') {
      locateResult.value = { found: false, snippet: '' }
      return
    }
    if (content.value && !content.value.isImage) {
      locateResult.value = findInSnapshotHtml(content.value.text, locator)
      return
    }
    locateResult.value = { found: false, snippet: '' }
  }

  watch(
    [taskId, snapshotId],
    () => {
      void reload()
    },
    { immediate: true },
  )

  return {
    view,
    loading,
    error,
    content,
    contentLoading,
    locateResult,
    sandboxHtml,
    searchQuery,
    reload,
    locate,
  }
}
