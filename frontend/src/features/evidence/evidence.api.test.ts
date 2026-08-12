import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchEvidenceContent, getEvidence } from './evidence.api'

vi.mock('@/app/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

import { apiClient } from '@/app/api/client'

const getMock = vi.mocked(apiClient.get)

describe('evidence.api', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.unstubAllGlobals())

  it('getEvidence hits /tasks/{id}/evidence/{snapshotId}', async () => {
    getMock.mockResolvedValue({ evidence_id: 1 } as never)
    await getEvidence('9', 42)
    expect(getMock).toHaveBeenCalledWith('/tasks/9/evidence/42')
  })

  it('fetchEvidenceContent reads stored bytes as text (never live source)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => 'text/html' },
        text: async () => '<html>历史快照</html>',
      }),
    )
    const content = await fetchEvidenceContent('/tasks/9/evidence/42/content')
    expect(content.isImage).toBe(false)
    expect(content.text).toBe('<html>历史快照</html>')
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledWith(
      expect.stringContaining('/api/tasks/9/evidence/42/content'),
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('fetchEvidenceContent maps image to object URL', async () => {
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:img') })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => 'image/png' },
        blob: async () => new Blob([new Uint8Array([1])], { type: 'image/png' }),
      }),
    )
    const content = await fetchEvidenceContent('/tasks/9/evidence/42/content')
    expect(content.isImage).toBe(true)
    expect(content.imageUrl).toBe('blob:img')
  })
})
