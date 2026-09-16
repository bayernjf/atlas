import { describe, expect, it } from 'vitest'
import { renderMarkers } from '../markers'
import type { Diagnostic } from '../diagnostics'

function diag(pointer: string, start: number, raw: string): Diagnostic {
  return {
    severity: 'error',
    layer: 'template',
    code: 'REF_NODE_NOT_FOUND',
    message: raw,
    loc: { nodeId: 'n-1', pointer, token: { start, end: start + raw.length, raw } },
  }
}

describe('renderMarkers 预留挂点 (U37)', () => {
  it('投影指定字段 pointer 上的 token 区间并保持入参顺序', () => {
    const diagnostics = [
      diag('/promptTemplate', 10, '{{b}}'),
      {
        severity: 'error',
        layer: 'field',
        code: 'FIELD_REQUIRED',
        message: '必填',
        loc: { nodeId: 'n-1', pointer: '/tool' },
      } satisfies Diagnostic,
      diag('/promptTemplate', 0, '{{a}}'),
    ]
    expect(renderMarkers(diagnostics, '/promptTemplate')).toEqual([
      { start: 10, end: 15, raw: '{{b}}' },
      { start: 0, end: 5, raw: '{{a}}' },
    ])
    expect(renderMarkers(diagnostics, '/tool')).toEqual([])
  })
})
