import { describe, expect, it } from 'vitest'
import { RunGeneration } from '../runGeneration'

describe('RunGeneration (U1382)', () => {
  it('hands out monotonically increasing generations', () => {
    const runs = new RunGeneration()
    expect(runs.next().generation).toBe(1)
    expect(runs.next().generation).toBe(2)
  })

  it('aborts the previous signal when a new generation starts', () => {
    const runs = new RunGeneration()
    const first = runs.next()
    expect(first.signal.aborted).toBe(false)
    const second = runs.next()
    expect(first.signal.aborted).toBe(true)
    expect(second.signal.aborted).toBe(false)
    expect(runs.isCurrent(first.generation)).toBe(false)
    expect(runs.isCurrent(second.generation)).toBe(true)
  })

  it('abort() invalidates the in-flight generation without opening a new one', () => {
    const runs = new RunGeneration()
    const first = runs.next()
    runs.abort()
    expect(first.signal.aborted).toBe(true)
    expect(runs.isCurrent(first.generation)).toBe(false)
    // 下一次 next 仍然严格递增，不与作废代撞号。
    expect(runs.next().generation).toBe(first.generation + 2)
  })

  it('treats late frames of a superseded run as stale including its terminal frame', () => {
    const runs = new RunGeneration()
    const old = runs.next()
    const next = runs.next()
    // 旧运行的终帧/迟到帧回调命中的判定。
    expect(runs.isCurrent(old.generation)).toBe(false)
    expect(runs.isCurrent(next.generation)).toBe(true)
  })
})
