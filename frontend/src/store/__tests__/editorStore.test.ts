import { describe, expect, it } from 'vitest'
import { nextId, useEditorStore, type EditorNode } from '../editorStore'
import { defaultConfig, defaultRetry } from '../../lib/nodeCatalog'

function stubNode(id: string): EditorNode {
  return {
    id,
    position: { x: 0, y: 0 },
    data: {
      label: id,
      kind: id.startsWith('ai_decision') ? 'ai_decision' : 'trigger',
      status: 'idle',
      config: defaultConfig('trigger'),
      retry: defaultRetry(),
    },
  }
}

describe('nextId', () => {
  it('avoids collision with seeded node ids', () => {
    expect(nextId('ai_decision', [stubNode('trigger-1'), stubNode('ai_decision-1')])).toBe(
      'ai_decision-2',
    )
  })

  it('starts at 1 for new kinds and ignores unrelated prefixes', () => {
    expect(nextId('tool_call', [stubNode('trigger-9')])).toBe('tool_call-1')
  })
})

describe('editorStore addNodeAt / variables', () => {
  it('adds node with unique id at given position and selects it', () => {
    useEditorStore.setState({
      nodes: [stubNode('trigger-1')],
      edges: [],
      variables: [],
      selectedNodeId: null,
      logs: [],
    })
    useEditorStore.getState().addNodeAt('trigger', { x: 300, y: 200 })

    const state = useEditorStore.getState()
    expect(state.nodes).toHaveLength(2)
    const added = state.nodes[1]
    expect(added.id).toBe('trigger-2')
    expect(added.position).toEqual({ x: 300, y: 200 })
    expect(added.data.config.triggerType).toBe('manual')
    expect(state.selectedNodeId).toBe('trigger-2')
  })

  it('deleting a node also removes its edges', () => {
    useEditorStore.setState({
      nodes: [stubNode('trigger-1'), stubNode('trigger-2')],
      edges: [
        { id: 'a', source: 'trigger-1', target: 'trigger-2' },
        { id: 'b', source: 'trigger-2', target: 'trigger-1' },
        { id: 'c', source: 'trigger-1', target: 'trigger-1' },
      ],
      variables: [],
      selectedNodeId: 'trigger-2',
      logs: [],
    })
    useEditorStore.getState().deleteSelectedNode()
    const remaining = useEditorStore.getState().edges.map((edge) => edge.id)
    expect(remaining).toEqual(['c'])
  })
})

describe('editorStore W9-W10 run status and draft loading', () => {
  it('marks and resets per-node run status', () => {
    useEditorStore.setState({
      nodes: [stubNode('trigger-1'), stubNode('ai_decision-1')],
      edges: [],
      variables: [],
      selectedNodeId: null,
      logs: [],
    })
    useEditorStore.getState().setNodeStatus('trigger-1', 'running')
    expect(useEditorStore.getState().nodes[0].data.status).toBe('running')
    useEditorStore.getState().setNodeStatus('trigger-1', 'completed')
    useEditorStore.getState().resetRunStatuses()
    expect(useEditorStore.getState().nodes[0].data.status).toBe('idle')
  })

  it('loadGraph replaces canvas with a serialized draft', () => {
    useEditorStore.getState().loadGraph({
      version: 1,
      variables: [{ name: 'approval_limit', type: 'number', value: '500', scope: 'global' }],
      nodes: [
        {
          id: 'trigger-1', type: 'trigger', name: '触发', description: '',
          position: { x: 1, y: 2 },
          config: { triggerType: 'webhook', webhookUrl: '/hooks/refund' },
          retry: defaultRetry(),
        },
        {
          id: 'tool_call-1', type: 'tool_call', name: '退款', description: '',
          position: { x: 3, y: 4 },
          config: { tool: 'shop/process_refund' },
          retry: defaultRetry(),
        },
      ],
      edges: [{ id: 'e1', source: 'trigger-1', target: 'tool_call-1' }],
    })
    const state = useEditorStore.getState()
    expect(state.nodes).toHaveLength(2)
    expect(state.nodes[1].data.label).toBe('退款')
    expect(state.nodes[1].data.status).toBe('idle')
    expect(state.edges).toEqual([{ id: 'e1', source: 'trigger-1', target: 'tool_call-1' }])
    expect(state.variables[0].name).toBe('approval_limit')
  })
})
