import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { computed, ref } from 'vue'
import { mount } from '@vue/test-utils'

import { buildSandboxHtml } from '@/features/evidence/sandbox'
import type { EvidenceContent, EvidenceView } from '@/features/evidence/types'
import TaskEvidenceView from './TaskEvidenceView.vue'

const mocks = vi.hoisted(() => ({
  view: null as EvidenceView | null,
  loading: false,
  error: null as string | null,
  content: null as EvidenceContent | null,
  locate: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { taskId: '9', evidenceId: '42' } }),
}))

vi.mock('@/features/evidence/useEvidence', () => ({
  useEvidence: () => ({
    view: ref(mocks.view),
    loading: ref(mocks.loading),
    error: ref(mocks.error),
    content: ref(mocks.content),
    contentLoading: ref(false),
    locateResult: ref(null),
    sandboxHtml: computed(() =>
      mocks.view?.display_mode === 'raw' && mocks.content && !mocks.content.isImage
        ? buildSandboxHtml(mocks.content.text)
        : '',
    ),
    searchQuery: ref(''),
    reload: vi.fn(),
    locate: mocks.locate,
  }),
}))

const BASE_VIEW: EvidenceView = {
  evidence_id: 42,
  task_id: 9,
  source_url: 'https://example.com/page',
  fetched_at: '2026-08-01T12:00:00Z',
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

describe('TaskEvidenceView 证据查看器', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.view = structuredClone(BASE_VIEW)
    mocks.loading = false
    mocks.error = null
    mocks.content = { text: '<html>历史快照</html>', contentType: 'text/html', isImage: false }
  })

  it('渲染来源/时间/展示模式与字段证据（历史事实）', () => {
    const wrapper = mount(TaskEvidenceView)
    expect(wrapper.text()).toContain('https://example.com/page')
    expect(wrapper.text()).toContain('提取正文')
    expect(wrapper.text()).toContain('company')
    expect(wrapper.text()).toContain('css')
    expect(wrapper.text()).toContain('0.95')
  })

  it('raw 模式使用 sandbox iframe（禁止脚本，不用 v-html）', () => {
    mocks.view = { ...BASE_VIEW, display_mode: 'raw' }
    mocks.content = { text: '<html><body>历史</body></html>', contentType: 'text/html', isImage: false }
    const wrapper = mount(TaskEvidenceView)
    const iframe = wrapper.find('iframe')
    expect(iframe.exists()).toBe(true)
    expect(iframe.attributes('sandbox')).toBe('')
    expect(iframe.attributes('srcdoc')).toContain('Content-Security-Policy')
    // 禁止 v-html / innerHTML 注入第三方 HTML
    const source = readFileSync(resolve(import.meta.dirname, 'TaskEvidenceView.vue'), 'utf-8')
    expect(source).not.toContain('v-html')
    expect(source).not.toContain('innerHTML')
  })

  it('snapshot 优先展示视觉快照', () => {
    mocks.view = {
      ...BASE_VIEW,
      display_mode: 'snapshot',
      mime_type: 'image/png',
      summary: null,
    }
    mocks.content = { text: '', contentType: 'image/png', isImage: true, imageUrl: 'blob:img' }
    const wrapper = mount(TaskEvidenceView)
    const img = wrapper.find('img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('blob:img')
  })

  it('只读：无任何编辑/审核动作', () => {
    const wrapper = mount(TaskEvidenceView)
    const labels = wrapper.findAll('button').map((b) => b.text().trim())
    expect(labels).not.toContain('通过')
    expect(labels).not.toContain('拒绝')
    expect(labels).not.toContain('修正')
  })
})
