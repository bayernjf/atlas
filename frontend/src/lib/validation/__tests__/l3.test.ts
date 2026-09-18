/**
 * L3 全图预判单测（M4 批 2 ⑥ / U41①②）。
 *
 * 对拍：l3-fixtures.json 由 scripts/dev/generate_l3_fixtures.py 以后端
 * parse_graph 实跑生成，后端 tests/test_l3_fixtures.py 守夹具不漂移；
 * 本文件断言前端 validateL3 的消息序列与后端结构域输出逐条一致。
 */
import { describe, expect, it } from 'vitest'
import fixtures from './l3-fixtures.json'
import type { ScopeEdgeLike, ScopeNodeLike } from '../../scope'
import {
  GRAPH_DATA_CYCLE_CODE,
  GRAPH_ILLEGAL_CYCLE_CODE,
  GRAPH_UNREACHABLE_CODE,
  loopBackedges,
  validateDataDependencyCycles,
  validateIllegalCycles,
  validateL3,
  validateReachability,
} from '../l3'

type FixtureCase = {
  name: string
  nodes: Array<{ id: string; type: string; config: Record<string, unknown> }>
  edges: ScopeEdgeLike[]
  backend: { structural: boolean; messages: string[] }
}

const cases = (fixtures as { cases: FixtureCase[] }).cases

function toScopeNodes(fixtureCase: FixtureCase): ScopeNodeLike[] {
  return fixtureCase.nodes.map((node) => ({
    id: node.id,
    kind: node.type,
    config: node.config,
  }))
}

describe('L3 与后端 dsl.py 对拍（共享夹具）', () => {
  for (const fixtureCase of cases) {
    it(`${fixtureCase.name}：消息序列与后端结构域输出逐条一致`, () => {
      const nodes = toScopeNodes(fixtureCase)
      const messages = validateL3(nodes, fixtureCase.edges).map((d) => d.message)
      expect(messages).toEqual(fixtureCase.backend.messages)
    })
  }

  it('每条诊断均为 layer:graph / severity:error 且 code 稳定', () => {
    for (const fixtureCase of cases) {
      for (const diagnostic of validateL3(toScopeNodes(fixtureCase), fixtureCase.edges)) {
        expect(diagnostic.layer).toBe('graph')
        expect(diagnostic.severity).toBe('error')
        expect([GRAPH_ILLEGAL_CYCLE_CODE, GRAPH_UNREACHABLE_CODE]).toContain(diagnostic.code)
      }
    }
  })
})

describe('validateReachability', () => {
  it('无 trigger 时跳过（同构后端 roots 空分支）', () => {
    const nodes: ScopeNodeLike[] = [
      { id: 'tool-a', kind: 'tool_call' },
      { id: 'tool-b', kind: 'tool_call' },
    ]
    const edges: ScopeEdgeLike[] = [{ source: 'tool-a', target: 'tool-b' }]
    expect(validateReachability(nodes, edges)).toEqual([])
  })

  it('不可达诊断按节点数组序、loc.nodeId 取不可达节点自身（供 Problems 定位）', () => {
    const nodes: ScopeNodeLike[] = [
      { id: 'trigger-1', kind: 'trigger' },
      { id: 'tool-ok', kind: 'tool_call' },
      { id: 'tool-z', kind: 'tool_call' },
      { id: 'tool-a', kind: 'tool_call' },
    ]
    const edges: ScopeEdgeLike[] = [{ source: 'trigger-1', target: 'tool-ok' }]
    const diagnostics = validateReachability(nodes, edges)
    expect(diagnostics.map((d) => d.loc.nodeId)).toEqual(['tool-z', 'tool-a'])
    expect(diagnostics.map((d) => d.code)).toEqual([
      GRAPH_UNREACHABLE_CODE,
      GRAPH_UNREACHABLE_CODE,
    ])
  })
})

describe('validateIllegalCycles / loopBackedges', () => {
  const loopNodes: ScopeNodeLike[] = [
    { id: 'trigger-1', kind: 'trigger' },
    {
      id: 'loop-1',
      kind: 'loop',
      config: { bodyTarget: 'tool-a', exitTarget: 'tool-exit' },
    },
    { id: 'tool-a', kind: 'tool_call' },
    { id: 'tool-b', kind: 'tool_call' },
    { id: 'tool-exit', kind: 'tool_call' },
  ]

  it('合法 loop 回边（含链式体内回边）进白名单，不报非法环', () => {
    const edges: ScopeEdgeLike[] = [
      { source: 'trigger-1', target: 'loop-1' },
      { source: 'loop-1', target: 'tool-a' },
      { source: 'tool-a', target: 'tool-b' },
      { source: 'tool-b', target: 'loop-1' },
      { source: 'loop-1', target: 'tool-exit' },
    ]
    const whitelist = loopBackedges(loopNodes, edges)
    // 只有真正有边回 loop 的 tool-b 进白名单；tool-a 无直连回边不登记。
    expect(whitelist.has('tool-b loop-1')).toBe(true)
    expect(whitelist.has('tool-a loop-1')).toBe(false)
    expect(validateIllegalCycles(loopNodes, edges, whitelist)).toEqual([])
  })

  it('exitTarget 缺失时不产白名单，回边按非法环预判（同构后端 442 行条件）', () => {
    const nodes: ScopeNodeLike[] = [
      { id: 'loop-1', kind: 'loop', config: { bodyTarget: 'tool-a', exitTarget: '' } },
      { id: 'tool-a', kind: 'tool_call' },
    ]
    const edges: ScopeEdgeLike[] = [
      { source: 'loop-1', target: 'tool-a' },
      { source: 'tool-a', target: 'loop-1' },
    ]
    expect(loopBackedges(nodes, edges).size).toBe(0)
    const diagnostics = validateIllegalCycles(nodes, edges, loopBackedges(nodes, edges))
    expect(diagnostics).toHaveLength(1)
    expect(diagnostics[0].code).toBe(GRAPH_ILLEGAL_CYCLE_CODE)
    expect(diagnostics[0].message).toContain('loop-1 → tool-a → loop-1')
  })

  it('白名单只摘除指定 (member, loop) 边，不影响其他成环路径', () => {
    // tool-a→loop-1 是合法回边；tool-a→tool-b→tool-a 是与 loop 无关的额外非法环。
    const edges: ScopeEdgeLike[] = [
      { source: 'trigger-1', target: 'loop-1' },
      { source: 'loop-1', target: 'tool-a' },
      { source: 'tool-a', target: 'tool-b' },
      { source: 'tool-b', target: 'tool-a' },
      { source: 'tool-b', target: 'loop-1' },
      { source: 'loop-1', target: 'tool-exit' },
    ]
    const diagnostics = validateIllegalCycles(loopNodes, edges, loopBackedges(loopNodes, edges))
    expect(diagnostics).toHaveLength(1)
    expect(diagnostics[0].message).toContain('tool-a → tool-b → tool-a')
  })
})

describe('GRAPH_DATA_CYCLE 数据依赖环（D30，同构后端 _validate_data_dependency_cycles）', () => {
  const loopEdges: ScopeEdgeLike[] = [
    { source: 'trigger-1', target: 'loop-1' },
    { source: 'loop-1', target: 'tool-body' },
    { source: 'tool-body', target: 'loop-1' },
    { source: 'loop-1', target: 'tool-exit' },
  ]

  function loopGraph(continueExpression: string, bodyParams: string): ScopeNodeLike[] {
    return [
      { id: 'trigger-1', kind: 'trigger' },
      {
        id: 'loop-1',
        kind: 'loop',
        config: {
          mode: 'while',
          continueExpression,
          maxIterations: 10,
          bodyTarget: 'tool-body',
          exitTarget: 'tool-exit',
        },
      },
      { id: 'tool-body', kind: 'tool_call', config: { tool: 'shop/x', params: bodyParams } },
      { id: 'tool-exit', kind: 'tool_call', config: { tool: 'message/send', params: '{}' } },
    ]
  }

  it('loop 条件引用体内节点、体内又引用 loop.index → 数据环（拓扑回边合法、补判）', () => {
    const nodes = loopGraph('{{tool-body.result}}', '{"idx":"{{loop-1.index}}"}')
    const diagnostics = validateDataDependencyCycles(nodes, loopEdges)
    expect(diagnostics).toHaveLength(1)
    const diagnostic = diagnostics[0]
    expect(diagnostic.code).toBe(GRAPH_DATA_CYCLE_CODE)
    expect(diagnostic.layer).toBe('graph')
    expect(diagnostic.severity).toBe('error')
    expect(diagnostic.message).toContain('loop-1 → tool-body → loop-1')
    // 回边引用方是体内节点，pointer 落其 params 模板字段
    expect(diagnostic.loc.nodeId).toBe('tool-body')
    expect(diagnostic.loc.pointer).toBe('/params')
  })

  it('合法 loop：条件只引用自身 index、体内引用 loop.index，不构成数据环', () => {
    const nodes = loopGraph('{{loop-1.index}} < 3', '{"idx":"{{loop-1.index}}"}')
    expect(validateDataDependencyCycles(nodes, loopEdges)).toEqual([])
    // 聚合进 validateL3 后也不应出现数据环（结构本身合法、可达）
    expect(validateL3(nodes, loopEdges).map((d) => d.code)).not.toContain(GRAPH_DATA_CYCLE_CODE)
  })

  it('线性上游引用（下游引用上游输出）是 DAG，不报数据环', () => {
    const nodes: ScopeNodeLike[] = [
      { id: 'trigger-1', kind: 'trigger' },
      { id: 'tool-a', kind: 'tool_call', config: { tool: 'x', params: '{}' } },
      {
        id: 'ai-1',
        kind: 'ai_decision',
        config: { promptTemplate: '看 {{tool-a.result}}' },
      },
    ]
    const edges: ScopeEdgeLike[] = [
      { source: 'trigger-1', target: 'tool-a' },
      { source: 'tool-a', target: 'ai-1' },
    ]
    expect(validateDataDependencyCycles(nodes, edges)).toEqual([])
  })
})
