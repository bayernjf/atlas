/**
 * 分层校验调度的 React 薄绑定（M4 批 2 ⑦ / U42，04 §6.5 末扩展条）。
 *
 * 在 Editor 挂载一次，消费 editorStore.dirty，驱动纯核 ValidationEngine：
 * - L1 字段层：useLayoutEffect 同步重算（编辑当帧即出红字/角标）；
 * - L2 引用层：setTimeout 300ms 防抖合并连续输入；
 * - L3 全图层：requestIdleCallback（不支持时 setTimeout 回退），结构签名未变不重算。
 * 产出写入独立 validationStore；revision 快照保护防止消费期间的新变更被漏清。
 */
import { useEffect, useLayoutEffect, useRef } from 'react'
import { useEditorStore } from '../../store/editorStore'
import { useValidationStore } from '../../store/validationStore'
import { useCardBindings, useToolInputSchemas, useToolOutputSchemas } from '../useScope'
import type { ScopeEdgeLike, ScopeNodeLike } from '../scope'
import { ValidationEngine, type EngineNode, type EngineNodeData } from './engine'
import { topologicalOrder } from './validateGraph'

/** L2 防抖窗口（04 §6.5：300ms）。 */
export const L2_DEBOUNCE_MS = 300
/** L3 idle 超时兜底：保证后台标签切回/繁忙时也能在时限内调度。 */
const L3_IDLE_TIMEOUT_MS = 500

function toScopeNode(node: EngineNode): ScopeNodeLike {
  return { id: node.id, kind: node.data.kind, config: node.data.config }
}

function toEngineNodes(
  nodes: Array<{ id: string; data: EngineNodeData }>,
): EngineNode[] {
  return nodes.map((node) => ({ id: node.id, data: node.data }))
}

type IdleHandle = number

const requestIdle: ((callback: () => void, options?: { timeout: number }) => number) | null =
  typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function'
    ? window.requestIdleCallback.bind(window)
    : null
const cancelIdleFn: ((handle: number) => void) | null =
  typeof window !== 'undefined' && typeof window.cancelIdleCallback === 'function'
    ? window.cancelIdleCallback.bind(window)
    : null

function scheduleIdle(callback: () => void): IdleHandle {
  if (requestIdle) return requestIdle(callback, { timeout: L3_IDLE_TIMEOUT_MS })
  return window.setTimeout(callback, 1)
}

function cancelIdle(handle: IdleHandle): void {
  if (cancelIdleFn) cancelIdleFn(handle)
  else window.clearTimeout(handle)
}

export function useValidationEngine(): void {
  const revision = useEditorStore((state) => state.dirty.revision)
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const variables = useEditorStore((state) => state.variables)
  const toolOutputSchemas = useToolOutputSchemas()
  const toolInputSchemas = useToolInputSchemas()
  const cardBindings = useCardBindings()

  const engineRef = useRef<ValidationEngine | null>(null)
  if (engineRef.current === null) engineRef.current = new ValidationEngine()
  const schemasRef = useRef(toolOutputSchemas)
  useEffect(() => {
    schemasRef.current = toolOutputSchemas
  }, [toolOutputSchemas])
  const inputSchemasRef = useRef(toolInputSchemas)
  useEffect(() => {
    inputSchemasRef.current = toolInputSchemas
  }, [toolInputSchemas])
  const cardBindingsRef = useRef(cardBindings)
  useEffect(() => {
    cardBindingsRef.current = cardBindings
  }, [cardBindings])

  // L1：同步层（编辑当帧）。
  useLayoutEffect(() => {
    const engine = engineRef.current!
    const store = useEditorStore.getState()
    const results = useValidationStore.getState()
    const snapRevision = store.dirty.revision
    const dueL1 = store.dirty.l1NodeIds

    const updated = engine.runL1(toEngineNodes(store.nodes), dueL1)
    if (updated.length > 0) {
      const entries = Object.fromEntries(
        updated.map((id) => [id, engine.getNodeDiagnostics(id)]),
      )
      results.patchNodes(entries, snapRevision)
    }
    if (dueL1.length > 0) {
      store.clearDirty({ revision: snapRevision, l1NodeIds: dueL1 })
    }
    engine.prune(store.nodes.map((node) => node.id))
    results.pruneNodes(store.nodes.map((node) => node.id))
  }, [revision, nodes])

  // L2：防抖层（300ms 合并连续输入）。
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const engine = engineRef.current!
      const store = useEditorStore.getState()
      const results = useValidationStore.getState()
      const snapRevision = store.dirty.revision
      const dueL2 = store.dirty.l2NodeIds

      const engineNodes = toEngineNodes(store.nodes)
      const dataById = new Map<string, EngineNodeData>(
        engineNodes.map((node) => [node.id, node.data]),
      )
      const updated = engine.runL2(
        engineNodes.map(toScopeNode),
        store.edges as ScopeEdgeLike[],
        store.variables,
        dataById,
        dueL2,
        schemasRef.current,
        inputSchemasRef.current,
        cardBindingsRef.current,
      )
      if (updated.length > 0) {
        const entries = Object.fromEntries(
          updated.map((id) => [id, engine.getNodeDiagnostics(id)]),
        )
        results.patchNodes(entries, snapRevision)
      }
      if (dueL2.length > 0) {
        store.clearDirty({ revision: snapRevision, l2NodeIds: dueL2 })
      }
      engine.prune(store.nodes.map((node) => node.id))
      results.pruneNodes(store.nodes.map((node) => node.id))
    }, L2_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [revision, nodes, edges, variables])

  // L3：空闲层（结构签名记忆化，未变结构不重算）。
  useEffect(() => {
    let cancelled = false
    const handle = scheduleIdle(() => {
      if (cancelled) return
      const engine = engineRef.current!
      const store = useEditorStore.getState()
      const results = useValidationStore.getState()
      const snapRevision = store.dirty.revision

      const scopeNodes = toEngineNodes(store.nodes).map(toScopeNode)
      const graphDiagnostics = engine.runGraph(
        scopeNodes,
        store.edges as ScopeEdgeLike[],
        store.variables,
      )
      const nodeOrder = topologicalOrder(scopeNodes, store.edges as ScopeEdgeLike[])
      results.setGraph(graphDiagnostics, nodeOrder, snapRevision)
      if (store.dirty.l3) {
        store.clearDirty({ revision: snapRevision, l3: true })
      }
    })
    return () => {
      cancelled = true
      cancelIdle(handle)
    }
  }, [revision, nodes, edges, variables])

  // 适配器 schema / 卡片目录到达或刷新：L2 全量补算一次（不消费 dirty）。
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const engine = engineRef.current!
      const store = useEditorStore.getState()
      const results = useValidationStore.getState()
      const engineNodes = toEngineNodes(store.nodes)
      const dataById = new Map<string, EngineNodeData>(
        engineNodes.map((node) => [node.id, node.data]),
      )
      const updated = engine.runL2(
        engineNodes.map(toScopeNode),
        store.edges as ScopeEdgeLike[],
        store.variables,
        dataById,
        store.nodes.map((node) => node.id),
        toolOutputSchemas,
        toolInputSchemas,
        cardBindings,
      )
      if (updated.length > 0) {
        results.patchNodes(
          Object.fromEntries(updated.map((id) => [id, engine.getNodeDiagnostics(id)])),
          store.dirty.revision,
        )
      }
    }, L2_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [toolOutputSchemas, toolInputSchemas, cardBindings])
}
