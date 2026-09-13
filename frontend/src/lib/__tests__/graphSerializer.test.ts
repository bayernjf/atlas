import { describe, expect, it } from 'vitest'
import type { Edge } from '@xyflow/react'
import { serializeGraph, type EditorNode } from '../graphSerializer'
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
