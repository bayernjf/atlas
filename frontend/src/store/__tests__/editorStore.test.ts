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

  it('numbers condition nodes with the condition- prefix', () => {
    expect(nextId('condition', [stubNode('trigger-1')])).toBe('condition-1')
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

  it('clears condition branch targets pointing at a deleted node', () => {
    const condition: EditorNode = {
      id: 'condition-1',
      position: { x: 0, y: 0 },
      data: {
        label: '路由',
        kind: 'condition',
        status: 'idle',
        config: {
          branches: [
            { label: '大额', expression: '{{amount}} > 1000', target: 'tool-human' },
            { label: '其他', expression: '{{amount}} > 100', target: 'tool-review' },
          ],
          defaultTarget: 'tool-human',
        },
        retry: defaultRetry(),
      },
    }
    useEditorStore.setState({
      nodes: [condition, stubNode('tool-review')],
      edges: [],
      variables: [],
      selectedNodeId: 'tool-review',
      logs: [],
    })
    useEditorStore.getState().deleteSelectedNode()
    const updated = useEditorStore.getState().nodes[0]
    expect(updated.data.config.branches?.[0].target).toBe('tool-human')
    expect(updated.data.config.branches?.[1].target).toBe('')
    expect(updated.data.config.defaultTarget).toBe('tool-human')
  })

  it('clears loop body/exit targets pointing at a deleted node', () => {
    const loop: EditorNode = {
      id: 'loop-1',
      position: { x: 0, y: 0 },
      data: {
        label: '重试循环',
        kind: 'loop',
        status: 'idle',
        config: {
          mode: 'while',
          continueExpression: '{{loop-1.index}} < 3',
          maxIterations: 10,
          bodyTarget: 'tool-body',
          exitTarget: 'tool-exit',
        },
        retry: defaultRetry(),
      },
    }
    useEditorStore.setState({
      nodes: [loop, stubNode('tool-body'), stubNode('tool-exit')],
      edges: [],
      variables: [],
      selectedNodeId: 'tool-exit',
      logs: [],
    })
    useEditorStore.getState().deleteSelectedNode()
    const updated = useEditorStore.getState().nodes[0]
    expect(updated.data.config.bodyTarget).toBe('tool-body')
    expect(updated.data.config.exitTarget).toBe('')
  })

  it('clears parallel branch and join targets pointing at a deleted node', () => {
    const parallel: EditorNode = {
      id: 'parallel-1',
      position: { x: 0, y: 0 },
      data: {
        label: '并行',
        kind: 'parallel',
        status: 'idle',
        config: {
          joinStrategy: 'all_success',
          branches: [
            { label: 'A', target: 'tool-a' },
            { label: 'B', target: 'tool-b' },
          ],
          joinTarget: 'tool-join',
        },
        retry: defaultRetry(),
      },
    }
    useEditorStore.setState({
      nodes: [parallel, stubNode('tool-a'), stubNode('tool-b'), stubNode('tool-join')],
      edges: [],
      variables: [],
      selectedNodeId: 'tool-b',
      logs: [],
    })
    useEditorStore.getState().deleteSelectedNode()
    const updated = useEditorStore.getState().nodes[0]
    expect(updated.data.config.branches?.[0].target).toBe('tool-a')
    expect(updated.data.config.branches?.[1].target).toBe('')
    expect(updated.data.config.joinTarget).toBe('tool-join')

    useEditorStore.setState({ selectedNodeId: 'tool-join' })
    useEditorStore.getState().deleteSelectedNode()
    expect(useEditorStore.getState().nodes[0].data.config.joinTarget).toBe('')
  })

  it('clears human approval targets pointing at a deleted node', () => {
    const human: EditorNode = {
      id: 'human-1',
      position: { x: 0, y: 0 },
      data: {
        label: '人工审批',
        kind: 'human_approval',
        status: 'idle',
        config: {
          summary: '退款审批',
          approver: '客服主管',
          timeoutSeconds: 300,
          onTimeout: 'reject',
          approvedTarget: 'tool-approve',
          rejectedTarget: 'tool-reject',
        },
        retry: defaultRetry(),
      },
    }
    useEditorStore.setState({
      nodes: [human, stubNode('tool-approve'), stubNode('tool-reject')],
      edges: [],
      variables: [],
      selectedNodeId: 'tool-reject',
      logs: [],
    })
    useEditorStore.getState().deleteSelectedNode()
    const updated = useEditorStore.getState().nodes[0]
    expect(updated.data.config.approvedTarget).toBe('tool-approve')
    expect(updated.data.config.rejectedTarget).toBe('')
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

describe('editorStore session breakpoints (04 §5.12)', () => {
  it('toggles breakpoint keys and stores a conditional expression', () => {
    useEditorStore.setState({ breakpoints: {} })
    const store = useEditorStore.getState()
    store.toggleBreakpoint('tool_call-1')
    expect(useEditorStore.getState().breakpoints).toEqual({ 'tool_call-1': {} })
    store.toggleBreakpoint('tool_call-1')
    expect(useEditorStore.getState().breakpoints).toEqual({})

    store.toggleBreakpoint('ai_decision-1')
    store.setBreakpointExpression('ai_decision-1', '{{trigger-1.x}} > 1')
    expect(useEditorStore.getState().breakpoints['ai_decision-1']).toEqual({
      expression: '{{trigger-1.x}} > 1',
    })
    useEditorStore.getState().clearBreakpoints()
    expect(useEditorStore.getState().breakpoints).toEqual({})
  })

  it('clears breakpoints when deleting the node or loading another graph', () => {
    useEditorStore.setState({
      nodes: [stubNode('trigger-1'), stubNode('tool_call-1')],
      edges: [],
      variables: [],
      selectedNodeId: 'trigger-1',
      logs: [],
      breakpoints: { 'trigger-1': {}, 'tool_call-1': { expression: 'x' } },
    })
    useEditorStore.getState().deleteSelectedNode()
    expect(useEditorStore.getState().breakpoints).toEqual({ 'tool_call-1': { expression: 'x' } })

    useEditorStore.getState().loadGraph({
      version: 1,
      variables: [],
      nodes: [
        {
          id: 'trigger-1', type: 'trigger', name: '触发', description: '',
          position: { x: 0, y: 0 },
          config: { triggerType: 'manual' },
          retry: defaultRetry(),
        },
      ],
      edges: [],
    })
    expect(useEditorStore.getState().breakpoints).toEqual({})
  })
})
