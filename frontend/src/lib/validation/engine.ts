/**
 * 分层校验引擎纯核（M4 批 2 ⑦ / U42，04 §6.5 末扩展条）。
 *
 * 无 React、无定时器，全部为同步纯计算（可直接 vitest）：
 * - L1 字段层：单节点 kind/label/config → 诊断，按节点签名记忆化，编辑即重算；
 * - L2 引用层：ScopeIndex 按**结构签名**记忆化（拓扑/loop 双 target/变量名不变则不重建），
 *   仅重算到期节点；toolOutputSchemas 到达或变化时该层缓存自动失效；
 * - L3 全图层：validateL3 同样按结构签名记忆化。
 *
 * 调度时序（L1 同步 / L2 防抖 / L3 requestIdleCallback）在 React 薄绑定
 * useValidationEngine 中；本类只回答「这批到期范围重算后更新了谁」。
 * 模板文本变化只改本节点 L2 签名，不重建 ScopeIndex（结构签名不含模板字段）。
 */
import type { CardBindings, JsonSchema, ScopeEdgeLike, ScopeIndex, ScopeNodeLike } from '../scope'
import { buildScopeIndex } from '../scope'
import type { NodeKind } from '../nodeCatalog'
import type { GraphVariable } from '../variables'
import type { Diagnostic } from './diagnostics'
import { rank } from './diagnostics'
import { validateNodeL1 } from './validateGraph'
import { validateL3 } from './l3'

export type EngineNodeData = {
  kind: NodeKind
  label: string
  config: Record<string, unknown>
}

export type EngineNode = {
  id: string
  data: EngineNodeData
}

/**
 * 结构签名：ScopeIndex 与 L3 共同依赖的最小投影——
 * 节点 id/kind、loop 的 bodyTarget/exitTarget（白名单与作用域区域）、边端点、全局变量名。
 * 不含模板文本/普通字段（只影响单节点 L1/L2）与节点位置。
 */
export function structureSignature(
  nodes: ScopeNodeLike[],
  edges: ScopeEdgeLike[],
  variables: Pick<GraphVariable, 'name'>[],
): string {
  return JSON.stringify({
    n: nodes.map((node) => ({
      id: node.id,
      kind: node.kind,
      bodyTarget: node.config?.bodyTarget ?? null,
      exitTarget: node.config?.exitTarget ?? null,
    })),
    e: edges.map((edge) => [edge.source, edge.target]),
    v: variables.map((variable) => variable.name),
  })
}

function configSignature(config: Record<string, unknown>): string {
  return JSON.stringify(config)
}

export class ValidationEngine {
  private structureSig: string | null = null
  private scope: ScopeIndex | null = null

  private readonly l1Sigs = new Map<string, string>()
  private readonly l2Sigs = new Map<string, string>()
  private readonly l1Cache = new Map<string, Diagnostic[]>()
  private readonly l2Cache = new Map<string, Diagnostic[]>()

  private graphSig: string | null = null
  private graphCache: Diagnostic[] = []
  private schemas: Record<string, JsonSchema> | null = null
  private cardBindingsKey: CardBindings | null = null

  private ensureScope(
    nodes: ScopeNodeLike[],
    edges: ScopeEdgeLike[],
    variables: Pick<GraphVariable, 'name'>[],
  ): { sig: string; scope: ScopeIndex } {
    const sig = structureSignature(nodes, edges, variables)
    if (sig !== this.structureSig || !this.scope) {
      this.structureSig = sig
      this.scope = buildScopeIndex(nodes, edges, variables)
      // 结构变了，L2 结果一律失效（L3 由 graphSig 单独记忆）。
      this.l2Sigs.clear()
      this.l2Cache.clear()
    }
    return { sig, scope: this.scope }
  }

  /**
   * L1：重算 dueIds（另自愈未缓存/签名漂移的节点），返回实际更新的节点 id。
   * 纯字段计算，不依赖作用域，编辑后同步调用。
   */
  runL1(nodes: EngineNode[], dueIds: string[]): string[] {
    const due = new Set(dueIds)
    const updated: string[] = []
    for (const node of nodes) {
      const sig = `${node.data.kind}|${node.data.label}|${configSignature(node.data.config)}`
      if (!due.has(node.id) && this.l1Sigs.get(node.id) === sig) continue
      this.l1Sigs.set(node.id, sig)
      this.l1Cache.set(node.id, validateNodeL1(node.id, node.data))
      updated.push(node.id)
    }
    return updated
  }

  /**
   * L2：记忆化 ScopeIndex，重算 dueIds（另自愈未缓存/签名漂移/schema 表变化）。
   * 返回实际更新的节点 id；config 投影与 scopeNodes 顺序必须一致（同一批节点）。
   */
  runL2(
    scopeNodes: ScopeNodeLike[],
    edges: ScopeEdgeLike[],
    variables: Pick<GraphVariable, 'name'>[],
    dataById: Map<string, EngineNodeData>,
    dueIds: string[],
    toolOutputSchemas?: Record<string, JsonSchema>,
    cardBindings?: CardBindings,
  ): string[] {
    const { sig, scope } = this.ensureScope(scopeNodes, edges, variables)
    const schemasKey = toolOutputSchemas ?? null
    if (schemasKey !== this.schemas) {
      // 适配器发现到达/刷新：工具深层路径可见性可能变化，L2 全部视为到期。
      this.schemas = schemasKey
      this.l2Sigs.clear()
      this.l2Cache.clear()
    }
    const cardKey = cardBindings ?? null
    if (cardKey !== this.cardBindingsKey) {
      // M8 卡片目录到达/刷新：卡片 bindings 引用可见性可能变化，L2 全部视为到期。
      this.cardBindingsKey = cardKey
      this.l2Sigs.clear()
      this.l2Cache.clear()
    }

    const due = new Set(dueIds)
    const updated: string[] = []
    for (const scopeNode of scopeNodes) {
      const data = dataById.get(scopeNode.id)
      if (!data) continue
      const nodeSig = `${sig}|${configSignature(data.config)}`
      if (!due.has(scopeNode.id) && this.l2Sigs.get(scopeNode.id) === nodeSig) continue
      this.l2Sigs.set(scopeNode.id, nodeSig)
      this.l2Cache.set(
        scopeNode.id,
        scope.validateRefsAt(scopeNode.id, data.kind, data.config, toolOutputSchemas, cardBindings),
      )
      updated.push(scopeNode.id)
    }
    return updated
  }

  /** L3：结构签名不变则直接返回缓存（编辑模板文本不触发全图重算）。 */
  runGraph(
    nodes: ScopeNodeLike[],
    edges: ScopeEdgeLike[],
    variables: Pick<GraphVariable, 'name'>[],
  ): Diagnostic[] {
    const sig = structureSignature(nodes, edges, variables)
    if (sig !== this.graphSig) {
      this.graphSig = sig
      this.graphCache = validateL3(nodes, edges)
    }
    return this.graphCache
  }

  /** 单节点 L1+L2 合并（L2 尚未跑到时只含 L1，体现分层渐进）。 */
  getNodeDiagnostics(id: string): Diagnostic[] {
    return rank([...(this.l1Cache.get(id) ?? []), ...(this.l2Cache.get(id) ?? [])])
  }

  getGraphDiagnostics(): Diagnostic[] {
    return this.graphCache
  }

  /** 删除已不存在节点的残留缓存（换图/删节点后调用）。 */
  prune(aliveIds: Iterable<string>): void {
    const alive = new Set(aliveIds)
    for (const map of [this.l1Sigs, this.l2Sigs, this.l1Cache, this.l2Cache]) {
      for (const id of map.keys()) {
        if (!alive.has(id)) map.delete(id)
      }
    }
  }
}
