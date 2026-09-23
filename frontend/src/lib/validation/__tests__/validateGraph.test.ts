import { describe, expect, it } from 'vitest'
import { defaultConfig, defaultRetry, type EditorNodeData, type NodeKind } from '../../nodeCatalog'
import { validateGraph, validateNodeDiagnostics, NODE_LABEL_REQUIRED_CODE } from '../validateGraph'
import type { GraphValidationNode } from '../validateGraph'
import { buildScopeIndex, type ScopeEdgeLike } from '../../scope'
import { FIELD_CODES } from '../l1'

function gnode(
  id: string,
  kind: NodeKind,
  config?: EditorNodeData['config'],
  label = '',
): GraphValidationNode {
  const data: EditorNodeData = {
    label,
    kind,
    status: 'idle',
    config: config ?? defaultConfig(kind),
    retry: defaultRetry(),
  }
  return { id, data }
}

const edge = (source: string, target: string): ScopeEdgeLike => ({ source, target })

describe('validateNodeDiagnostics 聚合 (U37⑤)', () => {
  it('聚合名称必填 + L1 字段诊断并补 loc.nodeId', () => {
    const node = gnode('ai-1', 'ai_decision', defaultConfig('ai_decision'), '   ')
    const diagnostics = validateNodeDiagnostics(node.id, node.data)
    const codes = diagnostics.map((d) => d.code)
    expect(codes).toContain(NODE_LABEL_REQUIRED_CODE)
    expect(codes).toContain(FIELD_CODES.PATTERN)
    const labelDiag = diagnostics.find((d) => d.code === NODE_LABEL_REQUIRED_CODE)
    expect(labelDiag).toMatchObject({ layer: 'field', message: '节点名称必填' })
    expect(labelDiag?.loc).toEqual({ nodeId: 'ai-1' })
    const fieldDiag = diagnostics.find((d) => d.code === FIELD_CODES.PATTERN)
    expect(fieldDiag?.loc.nodeId).toBe('ai-1')
    expect(fieldDiag?.loc.pointer).toBe('/promptTemplate')
  })

  it('接入 ScopeIndex 时包含 L2 模板引用诊断', () => {
    const node = gnode('ai-1', 'ai_decision', { promptTemplate: '{{ghost-1.x}}' }, 'AI')
    const scope = buildScopeIndex(
      [{ id: 'ai-1', kind: 'ai_decision', config: node.data.config }],
      [],
      [],
    )
    const diagnostics = validateNodeDiagnostics(node.id, node.data, { selfId: 'ai-1', scope })
    expect(diagnostics).toHaveLength(1)
    const l2 = diagnostics[0]
    expect(l2.layer).toBe('template')
    expect(l2.code).toBe('REF_NODE_NOT_FOUND')
    expect(l2.loc).toMatchObject({ nodeId: 'ai-1', pointer: '/promptTemplate' })
    expect(l2.loc.token?.raw).toBe('{{ghost-1.x}}')
  })

  it('合法节点零诊断', () => {
    const node = gnode('wait-1', 'wait', defaultConfig('wait'), '等待')
    expect(validateNodeDiagnostics(node.id, node.data)).toEqual([])
  })
})

describe('validateGraph 全图聚合 (U37⑤)', () => {
  it('按节点拓扑序排序：上游节点的诊断排在下游之前（与输入顺序无关）', () => {
    const nodes = [
      gnode('ai-1', 'ai_decision', defaultConfig('ai_decision'), ' '),
      gnode('trigger-1', 'trigger', defaultConfig('trigger'), ' '),
    ]
    const diagnostics = validateGraph(nodes, [edge('trigger-1', 'ai-1')])
    const nodeIds = diagnostics.map((d) => d.loc.nodeId)
    expect(nodeIds[0]).toBe('trigger-1')
    expect(nodeIds).toContain('ai-1')
    // 同节点内 pointer 排序：无 pointer 的名称诊断排在 /promptTemplate 之前。
    const aiDiagnostics = diagnostics.filter((d) => d.loc.nodeId === 'ai-1').map((d) => d.code)
    expect(aiDiagnostics[0]).toBe(NODE_LABEL_REQUIRED_CODE)
  })

  it('M2 不产 layer:graph 诊断；L1 与 L2 同图共存', () => {
    const nodes = [
      gnode('trigger-1', 'trigger', defaultConfig('trigger'), '入口'),
      gnode('ai-1', 'ai_decision', { promptTemplate: '{{ghost-1.x}}' }, 'AI'),
    ]
    const diagnostics = validateGraph(nodes, [edge('trigger-1', 'ai-1')])
    expect(diagnostics.some((d) => d.layer === 'graph')).toBe(false)
    const layers = new Set(diagnostics.map((d) => d.layer))
    expect(layers.has('field')).toBe(false) // promptTemplate 非空，无 L1
    expect(layers.has('template')).toBe(true)
    expect(diagnostics).toHaveLength(1)
  })

  it('合法图零诊断', () => {
    const nodes = [
      gnode('trigger-1', 'trigger', defaultConfig('trigger'), '入口'),
      gnode(
        'wait-1',
        'wait',
        { waitType: 'duration', durationSeconds: 5 },
        '等待',
      ),
    ]
    expect(validateGraph(nodes, [edge('trigger-1', 'wait-1')])).toEqual([])
  })
})

describe('condition rootScoped 隐藏字段不产诊断（D14，docs/48）', () => {
  it('llm 模式下默认分支的空 expression 不再报表达式错误', () => {
    const config = { ...defaultConfig('condition'), conditionMode: 'llm' as const }
    const node = gnode('cond-1', 'condition', config, '分流')
    const diagnostics = validateNodeDiagnostics(node.id, node.data)
    const pointers = diagnostics.map((d) => d.loc.pointer)
    expect(pointers).not.toContain('/branches/0/expression')
    expect(pointers).toContain('/branches/0/description')
  })

  it('rule 模式保留 expression 必填诊断，且不产 description 诊断', () => {
    const node = gnode('cond-1', 'condition', defaultConfig('condition'), '分流')
    const diagnostics = validateNodeDiagnostics(node.id, node.data)
    const pointers = diagnostics.map((d) => d.loc.pointer)
    expect(pointers).toContain('/branches/0/expression')
    expect(pointers).not.toContain('/branches/0/description')
  })
})
