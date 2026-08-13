import { describe, expect, it } from 'vitest'

import { can } from '@/app/actions/allowedActions'

describe('can', () => {
  it('only allows actions the backend returned', () => {
    expect(can('cancel', ['cancel'])).toBe(true)
    expect(can('pause', ['cancel'])).toBe(false)
    expect(can('delete', [])).toBe(false)
  })

  it('never guesses from local state strings', () => {
    // 前端不做本地状态猜测：allowed 为空时任何 action 都不可用。
    expect(can('pause', [])).toBe(false)
    expect(can('resume', ['submit', 'delete'])).toBe(false)
  })
})
