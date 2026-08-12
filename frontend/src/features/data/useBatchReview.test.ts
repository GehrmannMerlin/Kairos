import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { useBatchReview } from './useBatchReview'

vi.mock('./data.api', () => ({ batchReview: vi.fn() }))

import { batchReview } from './data.api'

const batchMock = vi.mocked(batchReview)

describe('useBatchReview', () => {
  beforeEach(() => vi.clearAllMocks())

  it('调 batch-review 并传入选中记录的 data_version', async () => {
    batchMock.mockResolvedValue({
      batch_operation_id: 'b1',
      results: [
        { record_id: 1, ok: true, partition: 'passed', error: null },
        { record_id: 2, ok: true, partition: 'passed', error: null },
      ],
    })
    const onDone = vi.fn()
    const { run } = useBatchReview('9', ref([1, 2]), ref({ 1: 0, 2: 3 }), onDone)
    const ok = await run('approve', '人工确认')
    expect(ok).toBe(true)
    expect(batchMock).toHaveBeenCalledWith(
      '9',
      expect.objectContaining({
        action: 'approve',
        record_ids: [1, 2],
        reason: '人工确认',
        expected_data_versions: { 1: 0, 2: 3 },
      }),
    )
    expect(onDone).toHaveBeenCalled()
  })

  it('部分失败时报单条错误并返回 false', async () => {
    batchMock.mockResolvedValue({
      batch_operation_id: 'b1',
      results: [
        { record_id: 1, ok: true, partition: 'passed', error: null },
        { record_id: 2, ok: false, partition: null, error: '记录已更新，请刷新后重试' },
      ],
    })
    const { run, error } = useBatchReview('9', ref([1, 2]), ref({ 1: 0, 2: 0 }), vi.fn())
    const ok = await run('approve')
    expect(ok).toBe(false)
    expect(error.value).toContain('2:记录已更新，请刷新后重试')
  })

  it('空选择不调用 API', async () => {
    const { run } = useBatchReview('9', ref([]), ref({}), vi.fn())
    expect(await run('approve')).toBe(false)
    expect(batchMock).not.toHaveBeenCalled()
  })
})
