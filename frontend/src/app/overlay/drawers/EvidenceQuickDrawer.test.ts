import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { mount } from '@vue/test-utils'

import type { EvidenceView } from '@/features/evidence/types'
import EvidenceQuickDrawer from './EvidenceQuickDrawer.vue'

const mocks = vi.hoisted(() => ({
  closeDrawer: vi.fn(),
  push: vi.fn(),
  view: null as EvidenceView | null,
  loading: false,
  error: null as string | null,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('@/app/overlay/drawer.store', () => ({ closeDrawer: mocks.closeDrawer }))
vi.mock('@/features/evidence/useEvidence', () => ({
  useEvidence: () => ({
    view: ref(mocks.view),
    loading: ref(mocks.loading),
    error: ref(mocks.error),
    content: ref(null),
    contentLoading: ref(false),
    locateResult: ref(null),
    sandboxHtml: ref(''),
    searchQuery: ref(''),
    reload: vi.fn(),
    locate: vi.fn(),
  }),
}))

const VIEW: EvidenceView = {
  evidence_id: 42,
  task_id: 9,
  source_url: 'https://example.com/page',
  fetched_at: null,
  snapshot_version: 1,
  tool: 'http',
  tool_version: 'm10.1',
  mime_type: 'text/html',
  http_status: 200,
  content_length: 100,
  display_mode: 'text',
  summary: '<td>上海自动化设备有限公司</td>',
  field_evidence: [
    {
      record_id: 1,
      field_name: 'company',
      value: '上海自动化设备有限公司',
      raw_snippet: '<td>上海自动化设备有限公司</td>',
      source_locator: 'table#biz tr td',
      extract_method: 'css',
      extractor_version: 'm11.1',
      confidence: 0.95,
    },
  ],
  has_content: true,
  download_url: '/tasks/9/evidence/42/content',
}

describe('EvidenceQuickDrawer 快速核验', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.view = structuredClone(VIEW)
    mocks.loading = false
    mocks.error = null
  })

  it('展示字段值/snippet/来源/方法/置信度', () => {
    const wrapper = mount(EvidenceQuickDrawer, { props: { payload: { taskId: '9', evidenceId: 42 } } })
    expect(wrapper.text()).toContain('company')
    expect(wrapper.text()).toContain('上海自动化设备有限公司')
    expect(wrapper.text()).toContain('css')
    expect(wrapper.text()).toContain('0.95')
  })

  it('“完整查看”进入完整证据查看器', async () => {
    const wrapper = mount(EvidenceQuickDrawer, { props: { payload: { taskId: '9', evidenceId: 42 } } })
    await wrapper.find('button').trigger('click')
    expect(mocks.closeDrawer).toHaveBeenCalled()
    expect(mocks.push).toHaveBeenCalledWith('/tasks/9/evidence/42')
  })
})
