import { describe, expect, it } from 'vitest'

import { parseTaskQuery } from '@/app/router/deepLinks'

describe('parseTaskQuery', () => {
  it('parses approval deep link', () => {
    expect(parseTaskQuery({ approval: 'a-123' }).approval).toBe('a-123')
  })

  it('parses data filter deep links', () => {
    const q = parseTaskQuery({ status: 'review', review_type: 'source_conflict' })
    expect(q.status).toBe('review')
    expect(q.review_type).toBe('source_conflict')
  })

  it('keeps known keys when extra query params exist', () => {
    const q = parseTaskQuery({ status: 'passed', source_type: 'official_site', x: 'y' })
    expect(q.status).toBe('passed')
    expect(q.source_type).toBe('official_site')
  })

  it('returns undefined for missing keys', () => {
    const q = parseTaskQuery({})
    expect(q.approval).toBeUndefined()
    expect(q.status).toBeUndefined()
  })

  it('accepts array query values', () => {
    expect(parseTaskQuery({ status: ['review'] }).status).toBe('review')
  })
})
