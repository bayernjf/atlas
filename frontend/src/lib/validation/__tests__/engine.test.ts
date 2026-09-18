/**
 * ValidationEngine 纯核单测（M4 批 2 ⑦ / U42）：分层记忆化与增量重算。
 * 调度时序（同步/防抖/idle）在 React 薄绑定，不在纯核测试范围。
 */
import { describe, expect, it } from 'vitest'
import { ValidationEngine, type EngineNode } from '../engine'
import type { ScopeEdgeLike } from '../../scope'

function node(id: string, kind: EngineNode['data']['kind'], config: Record<string, unknown> = {}): EngineNode {
  return { id, data: { kind, label: id, config } }
}

function scopeNodes(nodes: EngineNode[]) {
  return nodes.map((n) => ({ id: n.id, kind: n.data.kind, config: n.data.config }))
}

describe('ValidationEngine L1（字段层）', () => {
  it('首次全量、二次无变更不重算，改 label 后仅该节点重算', () => {
    const engine = new ValidationEngine()
    const nodes = [node('trigger-1', 'trigger', { triggerType: 'manual' }), node('tool-a', 'tool_call')]
    expect(engine.runL1(nodes, []).sort()).toEqual(['tool-a', 'trigger-1'])
    expect(engine.runL1(nodes, [])).toEqual([])

    const renamed = [
      nodes[0],
      { id: 'tool-a', data: { kind: 'tool_call' as const, label: '新名字', config: {} } },
    ]
    expect(engine.runL1(renamed, ['tool-a'])).toEqual(['tool-a'])
  })

  it('label 为空产出名称必填诊断', () => {
    const engine = new ValidationEngine()
    const nodes = [{ id: 'n1', data: { kind: 'tool_call' as const, label: '', config: {} } }]
    engine.runL1(nodes, ['n1'])
    const codes = engine.getNodeDiagnostics('n1').map((d) => d.code)
    expect(codes).toContain('NODE_LABEL_REQUIRED')
  })
})

describe('ValidationEngine L2（引用层）与 ScopeIndex 记忆化', () => {
  it('只改某节点模板文本：仅该节点 L2 重算，其他节点签名不变', () => {
    const engine = new ValidationEngine()
    const edges: ScopeEdgeLike[] = [{ source: 'trigger-1', target: 'tool-a' }]
    const make = (params: string) => [
      node('trigger-1', 'trigger', { triggerType: 'manual' }),
      node('tool-a', 'tool_call', { tool: 'demo/echo', params }),
    ]
    const first = make('')
    const dataById = new Map(first.map((n) => [n.id, n.data]))
    const updated1 = engine.runL2(scopeNodes(first), edges, [], dataById, first.map((n) => n.id))
    expect(updated1.sort()).toEqual(['tool-a', 'trigger-1'])

    const second = make('{{trigger-1.context.payload}}')
    const dataById2 = new Map(second.map((n) => [n.id, n.data]))
    // 只让 tool-a 到期；结构签名未变（模板文本不在结构投影里）。
    const updated2 = engine.runL2(scopeNodes(second), edges, [], dataById2, ['tool-a'])
    expect(updated2).toEqual(['tool-a'])
  })

  it('加边改变作用域：结构签名变化，L2 缓存整体失效', () => {
    const engine = new ValidationEngine()
    const nodes = [
      node('trigger-1', 'trigger', { triggerType: 'manual' }),
      node('tool-a', 'tool_call', { tool: 'demo/echo', params: '' }),
      node('tool-b', 'tool_call', { tool: 'demo/echo', params: '' }),
    ]
    const dataById = new Map(nodes.map((n) => [n.id, n.data]))
    engine.runL2(scopeNodes(nodes), [{ source: 'trigger-1', target: 'tool-a' }], [], dataById, [
      'trigger-1',
      'tool-a',
      'tool-b',
    ])
    // tool-b 此前不可达；加边 trigger-1→tool-b 后即使不放进 due，签名漂移也触发自愈重算。
    const updated = engine.runL2(
      scopeNodes(nodes),
      [
        { source: 'trigger-1', target: 'tool-a' },
        { source: 'trigger-1', target: 'tool-b' },
      ],
      [],
      dataById,
      [],
    )
    expect(updated.sort()).toEqual(['tool-a', 'tool-b', 'trigger-1'])
  })

  it('toolOutputSchemas 引用变化：L2 全部失效（深层路径可见性变化）', () => {
    const engine = new ValidationEngine()
    const nodes = [
      node('trigger-1', 'trigger', { triggerType: 'manual' }),
      node('tool-a', 'tool_call', { tool: 'demo/echo', params: '{{trigger-1.context.payload.x}}' }),
    ]
    const dataById = new Map(nodes.map((n) => [n.id, n.data]))
    engine.runL2(scopeNodes(nodes), [{ source: 'trigger-1', target: 'tool-a' }], [], dataById, [
      'trigger-1',
      'tool-a',
    ])
    const updated = engine.runL2(
      scopeNodes(nodes),
      [{ source: 'trigger-1', target: 'tool-a' }],
      [],
      dataById,
      [],
      { 'demo/echo': { type: 'object', properties: { x: { type: 'string' } } } },
    )
    expect(updated.sort()).toEqual(['tool-a', 'trigger-1'])
  })

  it('悬空引用产出 layer:template 诊断', () => {
    const engine = new ValidationEngine()
    const nodes = [
      node('trigger-1', 'trigger', { triggerType: 'manual' }),
      node('tool-a', 'tool_call', { tool: 'demo/echo', params: '{{ghost.result}}' }),
    ]
    const dataById = new Map(nodes.map((n) => [n.id, n.data]))
    engine.runL2(scopeNodes(nodes), [{ source: 'trigger-1', target: 'tool-a' }], [], dataById, [
      'trigger-1',
      'tool-a',
    ])
    expect(engine.getNodeDiagnostics('tool-a').some((d) => d.layer === 'template')).toBe(true)
  })
})

describe('ValidationEngine L3（全图结构层）', () => {
  it('结构不变命中缓存；加边后重算', () => {
    const engine = new ValidationEngine()
    const nodes = [
      node('trigger-1', 'trigger', { triggerType: 'manual' }),
      node('tool-orphan', 'tool_call'),
    ]
    const edges: ScopeEdgeLike[] = []
    const first = engine.runGraph(scopeNodes(nodes), edges, [])
    const second = engine.runGraph(scopeNodes(nodes), edges, [])
    expect(second).toBe(first)
    expect(second.map((d) => d.code)).toEqual(['GRAPH_UNREACHABLE'])

    const connected = engine.runGraph(
      scopeNodes(nodes),
      [{ source: 'trigger-1', target: 'tool-orphan' }],
      [],
    )
    expect(connected).toEqual([])
  })

  it('编辑不含引用的模板：图数据签名变化使 L3 重算，但无引用环时诊断仍为空', () => {
    const engine = new ValidationEngine()
    const edges = [{ source: 'trigger-1', target: 'tool-a' }]
    const v1 = [node('trigger-1', 'trigger', { triggerType: 'manual' }), node('tool-a', 'tool_call', { params: 'a' })]
    const first = engine.runGraph(scopeNodes(v1), edges, [])
    const v2 = [node('trigger-1', 'trigger', { triggerType: 'manual' }), node('tool-a', 'tool_call', { params: 'bbbb' })]
    const second = engine.runGraph(scopeNodes(v2), edges, [])
    // 模板投影进入图数据签名 → L3 重算（非同一数组）；ScopeIndex 仍按结构签名命中、不重建。
    expect(second).not.toBe(first)
    expect(second).toEqual([])
  })

  it('编辑模板引入数据环时 L3 实时报 GRAPH_DATA_CYCLE（模板投影必须进入图签名）', () => {
    const engine = new ValidationEngine()
    const edges: ScopeEdgeLike[] = [
      { source: 'trigger-1', target: 'loop-1' },
      { source: 'loop-1', target: 'tool-body' },
      { source: 'tool-body', target: 'loop-1' },
      { source: 'loop-1', target: 'tool-exit' },
    ]
    const build = (continueExpression: string) => [
      node('trigger-1', 'trigger', { triggerType: 'manual' }),
      node('loop-1', 'loop', {
        mode: 'while',
        continueExpression,
        maxIterations: 10,
        bodyTarget: 'tool-body',
        exitTarget: 'tool-exit',
      }),
      node('tool-body', 'tool_call', { tool: 'x', params: '{"idx":"{{loop-1.index}}"}' }),
      node('tool-exit', 'tool_call', { tool: 'y', params: '{}' }),
    ]
    // 初始合法：条件只引用自身 index，体内引用 loop.index 不成环。
    const clean = engine.runGraph(scopeNodes(build('{{loop-1.index}} < 3')), edges, [])
    expect(clean.map((d) => d.code)).not.toContain('GRAPH_DATA_CYCLE')
    // 仅改模板文本（结构签名不变）：条件改引体内节点 → 与体内的 loop.index 引用成环，须重算并报出。
    const cyclic = engine.runGraph(scopeNodes(build('{{tool-body.result}}')), edges, [])
    expect(cyclic.map((d) => d.code)).toContain('GRAPH_DATA_CYCLE')
    // 改回合法条件后环消失。
    const recovered = engine.runGraph(scopeNodes(build('{{loop-1.index}} < 3')), edges, [])
    expect(recovered.map((d) => d.code)).not.toContain('GRAPH_DATA_CYCLE')
  })
})

describe('ValidationEngine 缓存维护', () => {
  it('prune 清除已删节点的残留', () => {
    const engine = new ValidationEngine()
    const nodes = [
      node('trigger-1', 'trigger', { triggerType: 'manual' }),
      node('tool-dead', 'tool_call'),
    ]
    engine.runL1(nodes, nodes.map((n) => n.id))
    const dataById = new Map(nodes.map((n) => [n.id, n.data]))
    engine.runL2(scopeNodes(nodes), [], [], dataById, nodes.map((n) => n.id))
    engine.prune(['trigger-1'])
    expect(engine.getNodeDiagnostics('tool-dead')).toEqual([])
  })
})
