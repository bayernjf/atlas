import { describe, expect, it } from 'vitest'
import type { Edge } from '@xyflow/react'
import { deserializeGraph, serializeGraph, type EditorNode } from '../graphSerializer'
import { defaultConfig, defaultRetry } from '../nodeCatalog'
import type { GraphVariable } from '../variables'

const nodes: EditorNode[] = [
  {
    id: 'trigger-1',
    position: { x: 80.4, y: 100.9 },
    data: {
      label: '触发',
      kind: 'trigger',
      status: 'idle',
      config: { ...defaultConfig('trigger'), triggerType: 'manual' },
      retry: defaultRetry(),
    },
  },
]
const edges: Edge[] = [{ id: 'e1', source: 'trigger-1', target: 'tool_call-1' }]
const variables: GraphVariable[] = [
  { name: 'limit', type: 'number', value: '500', scope: 'global' },
]

describe('serializeGraph', () => {
  it('emits node_schema-shaped JSON with version, variables, nodes and edges', () => {
    const graph = serializeGraph(nodes, edges, variables)
    expect(graph.version).toBe(1)
    expect(graph.variables).toEqual(variables)
    expect(graph.edges).toEqual([{ id: 'e1', source: 'trigger-1', target: 'tool_call-1' }])
    expect(graph.nodes[0]).toMatchObject({
      id: 'trigger-1',
      type: 'trigger',
      name: '触发',
      config: { triggerType: 'manual' },
      retry: { onError: 'stop' },
    })
  })

  it('rounds positions to integers', () => {
    const graph = serializeGraph(nodes, edges, variables)
    expect(graph.nodes[0].position).toEqual({ x: 80, y: 101 })
  })
})

describe('deserializeGraph', () => {
  it('rebuilds editor nodes from backend graph JSON with idle status', () => {
    const graph = serializeGraph(nodes, edges, variables)
    const { nodes: restored, edges: restoredEdges, variables: restoredVars } = deserializeGraph(graph)
    expect(restored[0].id).toBe('trigger-1')
    expect(restored[0].data.kind).toBe('trigger')
    expect(restored[0].data.status).toBe('idle')
    expect(restored[0].data.label).toBe('触发')
    expect(restoredEdges).toEqual([{ id: 'e1', source: 'trigger-1', target: 'tool_call-1' }])
    expect(restoredVars).toEqual(variables)
  })

  it('round-trips condition branch config unchanged', () => {
    const conditionNode: EditorNode = {
      id: 'condition-1',
      position: { x: 0, y: 0 },
      data: {
        label: '金额路由',
        kind: 'condition',
        status: 'idle',
        config: {
          branches: [{ label: '大额', expression: '{{amount}} > 1000', target: 'tool-human' }],
          defaultTarget: 'tool-auto',
        },
        retry: defaultRetry(),
      },
    }
    const graph = serializeGraph([conditionNode], [], [])
    const { nodes: restored } = deserializeGraph(graph)
    expect(restored[0].data.kind).toBe('condition')
    expect(restored[0].data.config).toEqual(conditionNode.data.config)
  })
})

describe('serializeGraph debugSettings (docs/60 §5)', () => {
  it('omits debugSettings when breakpoints absent or empty (legacy shape)', () => {
    expect(serializeGraph(nodes, edges, variables).debugSettings).toBeUndefined()
    expect(serializeGraph(nodes, edges, variables, {}).debugSettings).toBeUndefined()
  })

  it('persists a plain line breakpoint as bare { nodeId }', () => {
    const graph = serializeGraph(nodes, edges, variables, { 'trigger-1': {} })
    expect(graph.debugSettings).toEqual({ breakpoints: [{ nodeId: 'trigger-1' }] })
  })

  it('trims blank/default fields while keeping configured ones', () => {
    const graph = serializeGraph(nodes, edges, variables, {
      'trigger-1': { expression: '   ', hitCount: 0, logMessage: '', onException: false },
      'tool_call-1': { expression: 'x > 1', hitCount: 3, logMessage: 'boom', onException: true },
    })
    expect(graph.debugSettings?.breakpoints).toEqual([
      { nodeId: 'trigger-1' },
      { nodeId: 'tool_call-1', expression: 'x > 1', hitCount: 3, logMessage: 'boom', onException: true },
    ])
  })

  it('round-trips breakpoints through deserializeGraph', () => {
    const graph = serializeGraph(nodes, edges, variables, {
      'trigger-1': { expression: 'a == 1', hitCount: 2, logMessage: 'm', onException: true },
    })
    const { debugSettings } = deserializeGraph(graph)
    expect(debugSettings).toEqual({
      breakpoints: [
        { nodeId: 'trigger-1', expression: 'a == 1', hitCount: 2, logMessage: 'm', onException: true },
      ],
    })
  })
})
