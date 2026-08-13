import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import ExportModal from './ExportModal.vue'

const mocks = vi.hoisted(() => ({
  closeModal: vi.fn(),
  exportArtifact: vi.fn(),
}))

vi.mock('@/app/overlay/modal.store', () => ({ closeModal: mocks.closeModal }))
vi.mock('@/features/artifacts/artifacts.api', () => ({
  exportArtifact: mocks.exportArtifact,
  artifactDownloadUrl: (taskId: string | number, artifactId: number) =>
    `/api/tasks/${taskId}/artifacts/${artifactId}/download`,
}))

const REF = { artifact_id: 11, content_hash: 'h', download_url: '/tasks/9/artifacts/11/download', row_count: 3 }

describe('ExportModal', () => {
  beforeEach(() => {
    mocks.closeModal.mockClear()
    mocks.exportArtifact.mockReset()
    mocks.exportArtifact.mockResolvedValue(REF)
  })

  it('发 formal + all 请求（无筛选）', async () => {
    const wrapper = mount(ExportModal, { props: { payload: { taskId: 9 } } })
    await wrapper.find('[data-testid="export-type"][value="formal"]').setValue()
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await new Promise((r) => setTimeout(r))
    expect(mocks.exportArtifact).toHaveBeenCalledWith(9, {
      export_type: 'formal',
      scope: 'all',
      filter: {},
    })
  })

  it('有筛选时 current 发带 filter 请求', async () => {
    const wrapper = mount(ExportModal, {
      props: { payload: { taskId: 9, filter: { q: '上海', field: '文号' } } },
    })
    await wrapper.find('[data-testid="export-scope-current"]').setValue()
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await new Promise((r) => setTimeout(r))
    expect(mocks.exportArtifact).toHaveBeenCalledWith(9, {
      export_type: 'formal',
      scope: 'current',
      filter: { q: '上海', field: '文号' },
    })
  })

  it('成功展示下载链接', async () => {
    const wrapper = mount(ExportModal, { props: { payload: { taskId: 9 } } })
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await new Promise((r) => setTimeout(r))
    const link = wrapper.find('[data-testid="export-download-link"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/api/tasks/9/artifacts/11/download')
  })
})
