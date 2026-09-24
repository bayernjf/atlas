import { describe, expect, it } from 'vitest'
import { nextId, useEditorStore, validateNodeId, type EditorNode } from '../editorStore'
import type { SerializedGraph } from '../../lib/graphSerializer'
import { defaultConfig, defaultRetry } from '../../lib/nodeCatalog'
import { INITIAL_DIRTY } from '../../lib/validation/dirty'

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

  it('applyQuickFix removes a dangling template ref token (M4 批 3 ⑪)', () => {
    const text = '前置 {{ghost-1.result.x}} 后置'
    const ai: EditorNode = {
      id: 'ai-1',
      position: { x: 0, y: 0 },
      data: {
        label: '决策',
        kind: 'ai_decision',
        status: 'idle',
        config: { promptTemplate: text } as EditorNode['data']['config'],
        retry: defaultRetry(),
      },
    }
    useEditorStore.setState({
      nodes: [ai],
      edges: [],
      variables: [],
      selectedNodeId: null,
      logs: [],
    })
    const start = text.indexOf('{{ghost-1.result.x}}')
    useEditorStore.getState().applyQuickFix('ai-1', '/promptTemplate', {
      start,
      end: start + '{{ghost-1.result.x}}'.length,
      raw: '{{ghost-1.result.x}}',
    })
    expect(useEditorStore.getState().nodes[0].data.config.promptTemplate).toBe('前置  后置')
  })

  it('deleting a node keeps template refs but narrows L2 to referrers (M4 批 3 ⑩)', () => {
    const ai: EditorNode = {
      id: 'ai-1',
      position: { x: 0, y: 0 },
      data: {
        label: '决策',
        kind: 'ai_decision',
        status: 'idle',
        config: { promptTemplate: '{{tool-1.result.x}}' } as EditorNode['data']['config'],
        retry: defaultRetry(),
      },
    }
    const condition: EditorNode = {
      id: 'condition-1',
      position: { x: 0, y: 0 },
      data: {
        label: '路由',
        kind: 'condition',
        status: 'idle',
        config: {
          branches: [{ label: 'A', expression: 'x', target: 'other-1' }],
          defaultTarget: 'tool-1',
        } as EditorNode['data']['config'],
        retry: defaultRetry(),
      },
    }
    useEditorStore.setState({
      nodes: [ai, condition, stubNode('tool-1'), stubNode('other-1')],
      edges: [],
      variables: [],
      selectedNodeId: 'tool-1',
      logs: [],
    })
    useEditorStore.getState().deleteSelectedNode()
    const state = useEditorStore.getState()
    // 模板引用保留（悬空后由 quickFix 处理）
    expect(state.nodes.find((node) => node.id === 'ai-1')?.data.config.promptTemplate).toBe(
      '{{tool-1.result.x}}',
    )
    // target 引用自动清空
    expect(state.nodes.find((node) => node.id === 'condition-1')?.data.config.defaultTarget).toBe('')
    // L2 精确收窄：仅引用方 ai-1/condition-1，other-1 不重算
    expect(state.dirty.l2NodeIds.sort()).toEqual(['ai-1', 'condition-1'])
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

  it('toggles exception breakpoints independently (docs/28 §3.2)', () => {
    useEditorStore.setState({ breakpoints: {} })
    const store = useEditorStore.getState()
    // 无普通断点时可独立开启纯异常断点（continue 模式仅在抛异常时停）
    store.toggleBreakpointException('ai_decision-1')
    expect(useEditorStore.getState().breakpoints['ai_decision-1']).toEqual({ onException: true })
    // 再关一次：整条为空则删除，不遗留无条件普通断点
    useEditorStore.getState().toggleBreakpointException('ai_decision-1')
    expect(useEditorStore.getState().breakpoints).toEqual({})
    // 可与条件表达式共存
    store.toggleBreakpoint('tool_call-1')
    useEditorStore.getState().setBreakpointExpression('tool_call-1', '{{x}} > 1')
    useEditorStore.getState().toggleBreakpointException('tool_call-1')
    expect(useEditorStore.getState().breakpoints['tool_call-1']).toEqual({
      expression: '{{x}} > 1',
      onException: true,
    })
    // 关闭异常后条件字段保留
    useEditorStore.getState().toggleBreakpointException('tool_call-1')
    expect(useEditorStore.getState().breakpoints['tool_call-1']).toEqual({
      expression: '{{x}} > 1',
    })
  })
})

describe('NL 参数警告（M3 表单化展示）', () => {
  it('setNlWarnings 写入，loadGraph 换图时清空', () => {
    useEditorStore
      .getState()
      .setNlWarnings(['节点「query-1」工具 database/query 参数缺少必填字段：sql'])
    expect(useEditorStore.getState().nlWarnings).toHaveLength(1)

    useEditorStore.getState().loadGraph({ version: 1, variables: [], nodes: [], edges: [] })
    expect(useEditorStore.getState().nlWarnings).toEqual([])
  })
})

describe('editorStore 校验脏标记（M4 批 1 ⑤）', () => {
  function resetWith(nodes: EditorNode[], selected: string | null) {
    useEditorStore.setState({
      nodes,
      edges: [],
      variables: [],
      selectedNodeId: selected,
      logs: [],
      dirty: INITIAL_DIRTY,
    })
    useEditorStore.getState().clearDirty() // 干净基线：l1/l2/l3 全空
  }

  it('改纯 config 字段只脏该节点 L1', () => {
    resetWith([stubNode('trigger-1')], 'trigger-1')
    useEditorStore.getState().updateSelectedConfig({ webhookUrl: '/hooks/x' })
    const { dirty } = useEditorStore.getState()
    expect(dirty.l1NodeIds).toEqual(['trigger-1'])
    expect(dirty.l2NodeIds).toEqual([])
    expect(dirty.l3).toBe(false)
    expect(dirty.revision).toBe(1)
  })

  it('改含 {{}} 引用的字段同时脏该节点 L2', () => {
    resetWith([stubNode('trigger-1')], 'trigger-1')
    useEditorStore.getState().updateSelectedConfig({ webhookUrl: '{{trigger-1.context.token}}' })
    const { dirty } = useEditorStore.getState()
    expect(dirty.l1NodeIds).toEqual(['trigger-1'])
    expect(dirty.l2NodeIds).toEqual(['trigger-1'])
  })

  it('addNodeAt 标记 L3 + 新节点 L1/L2', () => {
    resetWith([stubNode('trigger-1')], null)
    useEditorStore.getState().addNodeAt('wait', { x: 0, y: 0 })
    const { dirty } = useEditorStore.getState()
    expect(dirty.l3).toBe(true)
    expect(dirty.l1NodeIds).toContain('wait-1')
    expect(dirty.l2NodeIds).toContain('wait-1')
  })

  it('onConnect 标记 L3 + 边两端 L2', () => {
    resetWith([stubNode('trigger-1'), stubNode('trigger-2')], null)
    useEditorStore
      .getState()
      .onConnect({ source: 'trigger-1', target: 'trigger-2' } as Parameters<ReturnType<typeof useEditorStore.getState>['onConnect']>[0])
    const { dirty } = useEditorStore.getState()
    expect(dirty.l3).toBe(true)
    expect(dirty.l2NodeIds).toEqual(['trigger-1', 'trigger-2'])
  })

  it('loadGraph 标记 L3 + 全部节点 L1/L2', () => {
    resetWith([stubNode('trigger-1')], null)
    useEditorStore.getState().loadGraph({
      version: 1,
      variables: [],
      nodes: [
        { id: 'trigger-1', type: 'trigger', name: 'a', description: '', position: { x: 0, y: 0 }, config: { triggerType: 'manual' }, retry: defaultRetry() },
        { id: 'tool_call-1', type: 'tool_call', name: 'b', description: '', position: { x: 0, y: 0 }, config: { tool: 'x' }, retry: defaultRetry() },
      ],
      edges: [],
    })
    const { dirty } = useEditorStore.getState()
    expect(dirty.l3).toBe(true)
    expect(dirty.l1NodeIds).toEqual(['trigger-1', 'tool_call-1'])
    expect(dirty.l2NodeIds).toEqual(['trigger-1', 'tool_call-1'])
  })

  it('clearDirty 清空待算范围但保留 revision', () => {
    resetWith([stubNode('trigger-1')], 'trigger-1')
    useEditorStore.getState().updateSelectedConfig({ webhookUrl: '/x' })
    expect(useEditorStore.getState().dirty.revision).toBe(1)
    useEditorStore.getState().clearDirty()
    const { dirty } = useEditorStore.getState()
    expect(dirty.l1NodeIds).toEqual([])
    expect(dirty.l2NodeIds).toEqual([])
    expect(dirty.l3).toBe(false)
    expect(dirty.revision).toBe(1)
  })
})

describe('editorStore renameNode 重命名联动（D30/B3）', () => {
  function toolNode(): EditorNode {
    return {
      id: 'tool-1',
      position: { x: 0, y: 0 },
      data: {
        label: '工具',
        kind: 'tool_call',
        status: 'idle',
        config: { tool: 't/x', params: '{}' } as EditorNode['data']['config'],
        retry: defaultRetry(),
      },
    }
  }

  it('改 id 原子联动 target / 模板引用 / 边端点 / 选中态 / 断点', () => {
    const condition: EditorNode = {
      id: 'condition-1',
      position: { x: 0, y: 0 },
      data: {
        label: '路由',
        kind: 'condition',
        status: 'idle',
        config: {
          branches: [{ label: 'A', expression: 'true', target: 'tool-1' }],
          defaultTarget: 'tool-1',
        } as EditorNode['data']['config'],
        retry: defaultRetry(),
      },
    }
    const ai: EditorNode = {
      id: 'ai-1',
      position: { x: 0, y: 0 },
      data: {
        label: '决策',
        kind: 'ai_decision',
        status: 'idle',
        config: { promptTemplate: '{{tool-1.result.a}} {{ tool-1.result.b }}' } as EditorNode['data']['config'],
        retry: defaultRetry(),
      },
    }
    useEditorStore.setState({
      nodes: [toolNode(), condition, ai, stubNode('other-1')],
      edges: [
        { id: 'e1', source: 'tool-1', target: 'ai-1' },
        { id: 'e2', source: 'other-1', target: 'tool-1' },
      ],
      variables: [],
      selectedNodeId: 'tool-1',
      breakpoints: { 'tool-1': { expression: 'x' } as never },
      logs: [],
    })

    useEditorStore.getState().renameNode('tool-1', 'tool-z')
    const state = useEditorStore.getState()

    expect(state.nodes.some((n) => n.id === 'tool-1')).toBe(false)
    expect(state.nodes.some((n) => n.id === 'tool-z')).toBe(true)
    const cond = state.nodes.find((n) => n.id === 'condition-1')!
    expect(cond.data.config.branches![0].target).toBe('tool-z')
    expect(cond.data.config.defaultTarget).toBe('tool-z')
    const aiNode = state.nodes.find((n) => n.id === 'ai-1')!
    expect(aiNode.data.config.promptTemplate).toBe('{{tool-z.result.a}} {{ tool-z.result.b }}')
    expect(state.edges.find((e) => e.id === 'e1')).toMatchObject({ source: 'tool-z', target: 'ai-1' })
    expect(state.edges.find((e) => e.id === 'e2')).toMatchObject({ source: 'other-1', target: 'tool-z' })
    expect(state.selectedNodeId).toBe('tool-z')
    expect(Object.keys(state.breakpoints)).toEqual(['tool-z'])
    // 结构 + 跨节点引用：L3 置位、L2 全量
    expect(state.dirty.l3).toBe(true)
    expect(state.dirty.l2NodeIds).toContain('condition-1')
    expect(state.dirty.l2NodeIds).toContain('ai-1')
  })

  it('非法 id（重复 / 非法字符 / 保留字 / 空）拒绝且不改图', () => {
    useEditorStore.setState({
      nodes: [toolNode(), stubNode('other-1')],
      edges: [],
      variables: [],
      selectedNodeId: 'tool-1',
      logs: [],
    })
    for (const bad of ['other-1', 'has space', 'a.b', 'global', '   ']) {
      useEditorStore.getState().renameNode('tool-1', bad)
      const state = useEditorStore.getState()
      // 拒绝改名：整图 id 集合保持不变
      expect(state.nodes.map((n) => n.id).sort()).toEqual(['other-1', 'tool-1'])
    }
  })
})

describe('validateNodeId（D30/B3）', () => {
  it('合法 / 无变化放行，非法返回文案', () => {
    expect(validateNodeId('tool-9', 'tool-1', ['tool-1', 'other-1'])).toBeNull()
    expect(validateNodeId('tool-1', 'tool-1', ['tool-1'])).toBeNull()
    expect(validateNodeId('other-1', 'tool-1', ['tool-1', 'other-1'])).toContain('已存在')
    expect(validateNodeId('a b', 'tool-1', ['tool-1'])).toContain('字母')
    expect(validateNodeId('a.b', 'tool-1', ['tool-1'])).toContain('字母')
    expect(validateNodeId('global', 'tool-1', ['tool-1'])).toContain('保留字')
    expect(validateNodeId('  ', 'tool-1', ['tool-1'])).toContain('不能为空')
  })
})


describe('B 包断点增强（docs/27 §4.2，U135）', () => {
  it('setBreakpointHitCount 存正整数，null/<1 归一为 undefined', () => {
    useEditorStore.setState({ breakpoints: { 'tool-1': {} } })
    useEditorStore.getState().setBreakpointHitCount('tool-1', 3)
    expect(useEditorStore.getState().breakpoints['tool-1']?.hitCount).toBe(3)
    useEditorStore.getState().setBreakpointHitCount('tool-1', null)
    expect(useEditorStore.getState().breakpoints['tool-1']?.hitCount).toBeUndefined()
    useEditorStore.getState().setBreakpointHitCount('tool-1', 0)
    expect(useEditorStore.getState().breakpoints['tool-1']?.hitCount).toBeUndefined()
  })

  it('setBreakpointLogMessage 非空才记，空白归一为 undefined', () => {
    useEditorStore.setState({ breakpoints: { 'tool-1': {} } })
    useEditorStore.getState().setBreakpointLogMessage('tool-1', '到达退款节点')
    expect(useEditorStore.getState().breakpoints['tool-1']?.logMessage).toBe('到达退款节点')
    useEditorStore.getState().setBreakpointLogMessage('tool-1', '   ')
    expect(useEditorStore.getState().breakpoints['tool-1']?.logMessage).toBeUndefined()
  })

  it('hitCount/logMessage 与 expression 共存不互相覆盖', () => {
    useEditorStore.setState({ breakpoints: { 'tool-1': { expression: '{{global.x}} > 1' } } })
    useEditorStore.getState().setBreakpointHitCount('tool-1', 2)
    useEditorStore.getState().setBreakpointLogMessage('tool-1', 'hit')
    expect(useEditorStore.getState().breakpoints['tool-1']).toEqual({
      expression: '{{global.x}} > 1',
      hitCount: 2,
      logMessage: 'hit',
    })
  })
})


describe('loadGraph persisted breakpoints (docs/60 §5)', () => {
  function graphWith(debugSettings?: SerializedGraph['debugSettings']): SerializedGraph {
    return {
      version: 1,
      variables: [],
      nodes: [
        {
          id: 'trigger-1',
          type: 'trigger',
          name: '触发',
          description: '',
          position: { x: 0, y: 0 },
          config: defaultConfig('trigger'),
          retry: defaultRetry(),
        },
      ],
      edges: [],
      ...(debugSettings ? { debugSettings } : {}),
    }
  }

  it('restores configured breakpoints and drops orphan node breakpoints', () => {
    useEditorStore.getState().loadGraph(
      graphWith({
        breakpoints: [
          { nodeId: 'trigger-1', expression: 'a == 1', hitCount: 2, logMessage: 'm', onException: true },
          { nodeId: 'ghost-node' },
        ],
      }),
    )
    const breakpoints = useEditorStore.getState().breakpoints
    expect(Object.keys(breakpoints)).toEqual(['trigger-1'])
    expect(breakpoints['trigger-1']).toEqual({
      expression: 'a == 1',
      hitCount: 2,
      logMessage: 'm',
      onException: true,
    })
  })

  it('restores a plain line breakpoint as an empty entry', () => {
    useEditorStore.getState().loadGraph(graphWith({ breakpoints: [{ nodeId: 'trigger-1' }] }))
    expect(useEditorStore.getState().breakpoints).toEqual({ 'trigger-1': {} })
  })

  it('yields no breakpoints when the graph has no debugSettings', () => {
    useEditorStore.getState().loadGraph(graphWith())
    expect(useEditorStore.getState().breakpoints).toEqual({})
  })
})
