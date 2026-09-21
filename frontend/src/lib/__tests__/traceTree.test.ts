import { describe, expect, it } from 'vitest'
import type { TraceSpanNode } from '../apiClient'
import {
  MIN_BAR_WIDTH_PCT,
  flattenSpans,
  formatSpanAttrs,
  layoutWaterfall,
  spanBarColor,
  spanKindColor,
  spanKindLabelKey,
} from '../traceTree'

function span(partial: Partial<TraceSpanNode> & Pick<TraceSpanNode, 'name' | 'kind'>): TraceSpanNode {
  const { name, kind, ...rest } = partial
  return {
    traceId: 'tr1',
    spanId: name,
    name,
    kind,
    startedAt: '2026-09-21T00:00:00.000Z',
    durationMs: 0,
    status: 'ok',
    ...rest,
  }
}

describe('flattenSpans', () => {
  it('深度优先展平并标注 depth', () => {
    const root = span({
      name: 'run',
      kind: 'run',
      durationMs: 100,
      children: [
        span({
          name: 'node-1',
          kind: 'node',
          startedAt: '2026-09-21T00:00:00.010Z',
          durationMs: 50,
          children: [span({ name: 'tool-1', kind: 'tool', startedAt: '2026-09-21T00:00:00.020Z', durationMs: 10 })],
        }),
        span({ name: 'node-2', kind: 'node', startedAt: '2026-09-21T00:00:00.060Z', durationMs: 30 }),
      ],
    })
    const rows = flattenSpans(root)
    expect(rows.map((r) => r.span.name)).toEqual(['run', 'node-1', 'tool-1', 'node-2'])
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2, 1])
  })

  it('collapseInternal 折叠 internal span 整棵子树', () => {
    const root = span({
      name: 'run',
      kind: 'run',
      durationMs: 100,
      children: [
        span({ name: 'node-1', kind: 'node', durationMs: 10 }),
        span({
          name: 'subgraph-inner',
          kind: 'node',
          internal: true,
          durationMs: 5,
          children: [span({ name: 'deep-tool', kind: 'tool', durationMs: 2 })],
        }),
      ],
    })
    const names = flattenSpans(root, { collapseInternal: true }).map((r) => r.span.name)
    expect(names).toEqual(['run', 'node-1'])
  })

  it('空 root 返空数组', () => {
    expect(flattenSpans(null)).toEqual([])
    expect(flattenSpans(undefined)).toEqual([])
  })
})

describe('layoutWaterfall', () => {
  it('按 root 起止计算 left/width 百分比', () => {
    const root = span({
      name: 'run',
      kind: 'run',
      durationMs: 100,
      children: [
        span({ name: 'node-1', kind: 'node', startedAt: '2026-09-21T00:00:00.010Z', durationMs: 50 }),
      ],
    })
    const laid = layoutWaterfall(flattenSpans(root), root)
    expect(laid[0].leftPct).toBeCloseTo(0, 5)
    expect(laid[0].widthPct).toBeCloseTo(100, 5)
    expect(laid[1].leftPct).toBeCloseTo(10, 5)
    expect(laid[1].widthPct).toBeCloseTo(50, 5)
  })

  it('durationMs=0 给最小可见宽度', () => {
    const root = span({ name: 'run', kind: 'run', durationMs: 100, children: [
      span({ name: 'zero', kind: 'node', startedAt: '2026-09-21T00:00:00.020Z', durationMs: 0 }),
    ] })
    const laid = layoutWaterfall(flattenSpans(root), root)
    const zero = laid.find((r) => r.span.name === 'zero')!
    expect(zero.widthPct).toBe(MIN_BAR_WIDTH_PCT)
  })

  it('单 span root：left 0、width 100', () => {
    const root = span({ name: 'run', kind: 'run', durationMs: 42 })
    const laid = layoutWaterfall(flattenSpans(root), root)
    expect(laid).toHaveLength(1)
    expect(laid[0].leftPct).toBe(0)
    expect(laid[0].widthPct).toBe(100)
  })

  it('横条不越右界（子 span 超出 root 结束时 clamp）', () => {
    const root = span({ name: 'run', kind: 'run', durationMs: 100, children: [
      span({ name: 'overflow', kind: 'tool', startedAt: '2026-09-21T00:00:00.090Z', durationMs: 50 }),
    ] })
    const laid = layoutWaterfall(flattenSpans(root), root)
    const over = laid.find((r) => r.span.name === 'overflow')!
    expect(over.leftPct + over.widthPct).toBeLessThanOrEqual(100.0001)
  })

  it('root.durationMs=0（异常未 finish）以最大结束点兜底', () => {
    const root = span({
      name: 'run',
      kind: 'run',
      durationMs: 0,
      status: 'error',
      children: [
        span({ name: 'n1', kind: 'node', startedAt: '2026-09-21T00:00:00.000Z', durationMs: 30 }),
        span({ name: 'n2', kind: 'node', startedAt: '2026-09-21T00:00:00.030Z', durationMs: 20 }),
      ],
    })
    const laid = layoutWaterfall(flattenSpans(root), root)
    // 兜底 total=50ms：n2 起点 30 → 60%，宽 20 → 40%
    const n2 = laid.find((r) => r.span.name === 'n2')!
    expect(n2.leftPct).toBeCloseTo(60, 5)
    expect(n2.widthPct).toBeCloseTo(40, 5)
  })
})

describe('kind 映射与横条颜色', () => {
  it('已知 kind 返 i18n key，未知原样', () => {
    expect(spanKindLabelKey('run')).toBe('trace.kind.run')
    expect(spanKindLabelKey('task_dispatch')).toBe('trace.kind.taskDispatch')
    expect(spanKindLabelKey('future_kind')).toBe('future_kind')
  })

  it('kind 颜色映射与未知回退', () => {
    expect(spanKindColor('tool')).toBe('green')
    expect(spanKindColor('run')).toBe('geekblue')
    expect(spanKindColor('unknown')).toBe('default')
  })

  it('横条颜色：internal 灰、error 红、ok 蓝', () => {
    expect(spanBarColor(span({ name: 'a', kind: 'node', status: 'ok' }))).toBe('#1677ff')
    expect(spanBarColor(span({ name: 'b', kind: 'node', status: 'error' }))).toBe('#ff4d4f')
    expect(spanBarColor(span({ name: 'c', kind: 'node', internal: true, status: 'error' }))).toBe('#bfbfbf')
  })
})

describe('formatSpanAttrs', () => {
  it('无 attrs 返 null，有 attrs 返 JSON', () => {
    expect(formatSpanAttrs(span({ name: 'a', kind: 'node' }))).toBeNull()
    expect(formatSpanAttrs(span({ name: 'b', kind: 'node', attrs: {} }))).toBeNull()
    const json = formatSpanAttrs(span({ name: 'c', kind: 'tool', attrs: { tool: 'http/request', code: 500 } }))
    expect(json).toContain('"tool": "http/request"')
    expect(json).toContain('"code": 500')
  })
})
