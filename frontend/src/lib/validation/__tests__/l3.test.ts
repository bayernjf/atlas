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
  GRAPH_ILLEGAL_CYCLE_CODE,
  GRAPH_UNREACHABLE_CODE,
  loopBackedges,
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
