import { describe, expect, it } from 'vitest'
import { extractEmailToken, formatCreatedAt, formatRemaining, remainingSeconds } from '../approvals'

describe('extractEmailToken', () => {
  it('extracts the token from /approvals/<token> paths', () => {
    expect(extractEmailToken('/approvals/eyJhbGciOiJIUzM4NCJ9.sig')).toBe('eyJhbGciOiJIUzM4NCJ9.sig')
  })

  it('returns null for non-approvals paths', () => {
    expect(extractEmailToken('/dashboard')).toBeNull()
    expect(extractEmailToken('/')).toBeNull()
  })

  it('takes only the first path segment and ignores trailing parts', () => {
    expect(extractEmailToken('/approvals/tok-123/extra')).toBe('tok-123')
  })

  it('returns null for the bare prefix', () => {
    expect(extractEmailToken('/approvals/')).toBeNull()
    expect(extractEmailToken('/approvals')).toBeNull()
  })

  it('decodes percent-encoded tokens', () => {
    expect(extractEmailToken('/approvals/a.b%2Fc')).toBe('a.b/c')
  })
})

describe('remainingSeconds', () => {
  it('computes the remaining window', () => {
    expect(remainingSeconds(1000, 300, 1200)).toBe(100)
  })

  it('clamps to zero once timed out', () => {
    expect(remainingSeconds(1000, 300, 2000)).toBe(0)
  })

  it('returns the full timeout at creation time', () => {
    expect(remainingSeconds(1000, 300, 1000)).toBe(300)
  })
})

describe('formatRemaining', () => {
  it('reports expired for zero or negative input', () => {
    expect(formatRemaining(0)).toBe('已超时')
    expect(formatRemaining(-5)).toBe('已超时')
  })

  it('formats sub-minute values in seconds', () => {
    expect(formatRemaining(45)).toBe('45 秒')
  })

  it('formats whole minutes without seconds', () => {
    expect(formatRemaining(120)).toBe('2 分')
  })

  it('formats mixed minutes and seconds', () => {
    expect(formatRemaining(150)).toBe('2 分 30 秒')
  })
})

describe('formatCreatedAt', () => {
  it('returns empty string for zero', () => {
    expect(formatCreatedAt(0)).toBe('')
  })

  it('formats epoch seconds as a local date string', () => {
    const formatted = formatCreatedAt(1_700_000_000)
    expect(formatted).toContain('2023')
  })
})
