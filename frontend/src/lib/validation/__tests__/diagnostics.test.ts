import { describe, expect, it } from 'vitest'
import { rank, type Diagnostic } from '../diagnostics'

function diag(partial: Partial<Diagnostic> & { code: string }): Diagnostic {
  return {
    severity: 'error',
    layer: 'field',
    message: partial.code,
    loc: {},
    ...partial,
  }
}

describe('rank diagnostics (U37①)', () => {
  it('puts errors before warnings', () => {
    const sorted = rank([
      diag({ code: 'w1', severity: 'warning' }),
      diag({ code: 'e1', severity: 'error' }),
    ])
    expect(sorted.map((d) => d.code)).toEqual(['e1', 'w1'])
  })

  it('orders by node topological order (upstream first), unknown nodes last', () => {
    const sorted = rank(
      [
        diag({ code: 'c', loc: { nodeId: 'c' } }),
        diag({ code: 'b', loc: { nodeId: 'b' } }),
        diag({ code: 'a', loc: { nodeId: 'a' } }),
        diag({ code: 'x', loc: { nodeId: 'x' } }),
      ],
      ['a', 'b', 'c'],
    )
    expect(sorted.map((d) => d.code)).toEqual(['a', 'b', 'c', 'x'])
  })

  it('orders same node by pointer then token start', () => {
    const sorted = rank(
      [
        diag({ code: 'b', loc: { nodeId: 'a', pointer: '/z', token: { start: 0, end: 2, raw: '{{b}}' } } }),
        diag({ code: 'a', loc: { nodeId: 'a', pointer: '/a' } }),
        diag({ code: 't2', loc: { nodeId: 'a', pointer: '/promptTemplate', token: { start: 10, end: 14, raw: '{{t2}}' } } }),
        diag({ code: 't1', loc: { nodeId: 'a', pointer: '/promptTemplate', token: { start: 0, end: 4, raw: '{{t1}}' } } }),
      ],
      ['a'],
    )
    expect(sorted.map((d) => d.code)).toEqual(['a', 't1', 't2', 'b'])
  })

  it('is stable and does not mutate the input', () => {
    const input = [diag({ code: 'x' }), diag({ code: 'y' })]
    const output = rank(input)
    expect(output).not.toBe(input)
    expect(input.map((d) => d.code)).toEqual(['x', 'y'])
    expect(output.map((d) => d.code)).toEqual(['x', 'y'])
  })
})
