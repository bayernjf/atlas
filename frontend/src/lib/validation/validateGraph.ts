/**
 * 图校验聚合入口（M2，M4 批 2 扩 L3；08 M2/M4 立项条 / 04 §6.5）。
 *
 * 单节点 = 节点名称必填 + L1 字段诊断（schema 解释器 + 手写跨字段）+ L2 模板引用诊断；
 * 全图 = 对每个节点跑一遍共享的 ScopeIndex，再叠加 L3 结构预判（不可达/非法环，
 * M4 批 2 同构 dsl.py；后端仍是唯一权威），最后按 rank（error 优先 → 节点拓扑序 →
 * pointer → token.start）排序。
 */

import type { RefValidationContext, EditorNodeData, NodeKind } from '../nodeCatalog'
import { validateNodeFields } from './l1'
import {
  buildScopeIndex,
  type CardBindings,
  type JsonSchema,
  type ScopeEdgeLike,
  type ScopeNodeLike,
} from '../scope'
import type { GraphVariable } from '../variables'
import { rank, type Diagnostic } from './diagnostics'
import { validateL3 } from './l3'
import { NODE_UI_SCHEMAS } from '../forms/nodeUiSchemas'
import { nestedHiddenFields } from '../forms/uiSchema'

/** 节点名称必填（data.label，非 config 字段；loc 仅带 nodeId）。 */
export const NODE_LABEL_REQUIRED_CODE = 'NODE_LABEL_REQUIRED'

export type GraphValidationNode = {
  id: string
  data: EditorNodeData
}

/**
 * 单节点 L1 诊断（名称必填 + 字段层），供分层调度引擎同步层直接调用（M4 批 2 ⑦）。
 * L1 诊断在此补 loc.nodeId；返回结果经 rank 排序。
 */
export function validateNodeL1(
  id: string,
  data: Pick<EditorNodeData, 'kind' | 'label' | 'config'>,
): Diagnostic[] {
  const diagnostics: Diagnostic[] = []

  if (!data.label.trim()) {
    diagnostics.push({
      severity: 'error',
      layer: 'field',
      code: NODE_LABEL_REQUIRED_CODE,
      message: '节点名称必填',
      loc: { nodeId: id },
    })
  }

  for (const diagnostic of filterNestedHidden(
    data.kind,
    data.config,
    validateNodeFields(data.kind, data.config),
  )) {
    diagnostics.push({ ...diagnostic, loc: { ...diagnostic.loc, nodeId: id } })
  }

  return rank(diagnostics)
}

/**
 * 剔除 rootScoped 隐藏行内字段的诊断（D14：condition 分支行按 conditionMode
 * 在 expression / description 间切换；渲染不显示的字段也不能产校验错误）。
 */
function filterNestedHidden(kind: NodeKind, config: EditorNodeData['config'], diagnostics: Diagnostic[]): Diagnostic[] {
  const uiSchema = NODE_UI_SCHEMAS[kind]
  if (!uiSchema?.hiddenWhen?.some((rule) => rule.rootScoped)) return diagnostics
  // conditionMode 缺省按 rule、wait durationMode 缺省按 static 求值
  // （与后端 DSL 默认语义一致），避免 fail-safe 全隐误伤
  const effectiveConfig =
    kind === 'condition' && config.conditionMode == null
      ? { ...config, conditionMode: 'rule' }
      : kind === 'wait' && config.durationMode == null
        ? { ...config, durationMode: 'static' }
        : config
  const hidden = nestedHiddenFields(uiSchema, effectiveConfig)
  if (hidden.size === 0) return diagnostics
  return diagnostics.filter((diagnostic) => {
    const pointer = diagnostic.loc.pointer
    if (!pointer) return true
    const leaf = decodeURIComponent(pointer.slice(pointer.lastIndexOf('/') + 1))
    return !hidden.has(leaf)
  })
}

/**
 * 单节点全部诊断（L1 + L2）。L2 由 ScopeIndex 自带 nodeId/pointer/token。
 */
export function validateNodeDiagnostics(
  id: string,
  data: EditorNodeData,
  refContext?: RefValidationContext,
): Diagnostic[] {
  const diagnostics = validateNodeL1(id, data)

  if (refContext) {
    diagnostics.push(
      ...refContext.scope.validateRefsAt(
        refContext.selfId,
        data.kind,
        data.config as Record<string, unknown>,
        refContext.toolOutputSchemas,
        refContext.toolInputSchemas,
        refContext.cardBindings,
      ),
    )
  }

  return rank(diagnostics)
}

/** Kahn 拓扑序（上游在前）；成环节点（如循环回边）在末尾按输入顺序补入。 */
export function topologicalOrder(nodes: ScopeNodeLike[], edges: ScopeEdgeLike[]): string[] {
  const ids = new Set(nodes.map((node) => node.id))
  const indegree = new Map<string, number>([...ids].map((id) => [id, 0]))
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue
    adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target])
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1)
  }

  const order: string[] = []
  const queue = nodes.map((node) => node.id).filter((id) => (indegree.get(id) ?? 0) === 0)
  const enqueued = new Set(queue)
  while (queue.length > 0) {
    const id = queue.shift()!
    order.push(id)
    for (const next of adjacency.get(id) ?? []) {
      const degree = (indegree.get(next) ?? 0) - 1
      indegree.set(next, degree)
      if (degree === 0 && !enqueued.has(next)) {
        queue.push(next)
        enqueued.add(next)
      }
    }
  }
  for (const node of nodes) {
    if (!order.includes(node.id)) order.push(node.id)
  }
  return order
}

/**
 * 全图前端诊断（L1 + L2 + L3）。一次构建 ScopeIndex 供全部节点复用，
 * 排序节点序按边拓扑推导；L3（不可达/非法环）同构后端 dsl.py，仅实时预判。
 */
export function validateGraph(
  nodes: GraphValidationNode[],
  edges: ScopeEdgeLike[],
  variables: Pick<GraphVariable, 'name'>[] = [],
  toolOutputSchemas?: Record<string, JsonSchema>,
  toolInputSchemas?: Record<string, JsonSchema>,
  cardBindings?: CardBindings,
): Diagnostic[] {
  const scopeNodes: ScopeNodeLike[] = nodes.map((node) => ({
    id: node.id,
    kind: node.data.kind,
    config: node.data.config as Record<string, unknown>,
  }))
  const scope = buildScopeIndex(scopeNodes, edges, variables)
  const nodeOrder = topologicalOrder(scopeNodes, edges)

  const diagnostics: Diagnostic[] = []
  for (const node of nodes) {
    diagnostics.push(
      ...validateNodeDiagnostics(
        node.id,
        node.data,
        { selfId: node.id, scope, toolOutputSchemas, toolInputSchemas, cardBindings },
      ),
    )
  }
  diagnostics.push(...validateL3(scopeNodes, edges))
  return rank(diagnostics, nodeOrder)
}
