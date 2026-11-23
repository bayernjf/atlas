/**
 * 分层校验调度的 React 薄绑定（M4 批 2 ⑦ / U42，04 §6.5 末扩展条）。
 *
 * 在 Editor 挂载一次，消费 editorStore.dirty，驱动纯核 ValidationEngine：
 * - L1 字段层：useLayoutEffect 同步重算（编辑当帧即出红字/角标）；
 * - L2 引用层：setTimeout 300ms 防抖合并连续输入；
 * - L3 全图层：requestIdleCallback（不支持时 setTimeout 回退）先调度，再把
 *   validateL3 投到专用 Web Worker；Worker 不可用/报错/克隆失败时回退主线程同步，
 *   结构签名未变不重算。
 * 产出写入独立 validationStore；revision 快照保护防止消费期间的新变更被漏清。
 */
import { useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import { useEditorStore } from '../../store/editorStore'
import { useValidationStore } from '../../store/validationStore'
import { useCardBindings, useToolInputSchemas, useToolOutputSchemas } from '../useScope'
import type { ScopeEdgeLike, ScopeNodeLike } from '../scope'
import {
  ValidationEngine,
  graphDataSignature,
  type EngineNode,
  type EngineNodeData,
} from './engine'
import { createL3WorkerClient, validateL3WithFallback, type L3WorkerClient } from './l3WorkerClient'
import type { Diagnostic } from './diagnostics'
import { topologicalOrder } from './validateGraph'
import { getLanguage, subscribe } from '../../locales'

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
  // docs/17：订阅当前语言，切换时全量重算（L1 诊断已 i18n）。
  const language = useSyncExternalStore(subscribe, getLanguage)

  const engineRef = useRef<ValidationEngine | null>(null)
  if (engineRef.current === null) engineRef.current = new ValidationEngine()
  const workerRef = useRef<L3WorkerClient | null | undefined>(undefined)
  const getL3Worker = (): L3WorkerClient | null => {
    if (workerRef.current === undefined) {
      try {
        workerRef.current = typeof Worker === 'undefined' ? null : createL3WorkerClient()
      } catch {
        // CSP/构造失败/浏览器禁 Worker：直接走同步回退，不把校验可用性打掉。
        workerRef.current = null
      }
    }
    return workerRef.current
  }
  useEffect(() => {
    return () => {
      workerRef.current?.dispose()
      workerRef.current = undefined
    }
  }, [])
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

  // L3：空闲层调度 + Worker 计算（结构签名记忆化，未变结构不重算）。
  useEffect(() => {
    let cancelled = false
    const engine = engineRef.current!
    const store = useEditorStore.getState()
    const scopeNodes = toEngineNodes(store.nodes).map(toScopeNode)
    const edges = store.edges as ScopeEdgeLike[]
    const variables = store.variables
    const sig = graphDataSignature(scopeNodes, edges, variables)

    const handle = scheduleIdle(() => {
      if (cancelled) return
      const apply = (graphDiagnostics: Diagnostic[]) => {
        if (cancelled) return
        const latest = useEditorStore.getState()
        const latestScopeNodes = toEngineNodes(latest.nodes).map(toScopeNode)
        const latestEdges = latest.edges as ScopeEdgeLike[]
        // 响应到达前图又变了：丢弃旧结果，交给下一轮 effect。
        if (graphDataSignature(latestScopeNodes, latestEdges, latest.variables) !== sig) return
        engine.setGraphResult(sig, graphDiagnostics)
        const results = useValidationStore.getState()
        const nodeOrder = topologicalOrder(latestScopeNodes, latestEdges)
        results.setGraph(graphDiagnostics, nodeOrder, latest.dirty.revision)
        if (latest.dirty.l3) {
          latest.clearDirty({ revision: latest.dirty.revision, l3: true })
        }
      }

      if (engine.getGraphSignature() === sig) {
        apply(engine.getGraphDiagnostics())
        return
      }
      void validateL3WithFallback(
        getL3Worker(),
        scopeNodes,
        edges,
        () => engine.runGraph(scopeNodes, edges, variables),
      )
        .then(apply)
        .catch(() => {
          // Worker 与同步回退都失败：保留上一轮图诊断，等下次编辑重试。
        })
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

  // 语言切换：清缓存并对全部节点重算 L1/L2 与 L3（常规 L1/L2 effect 只处理 dirty 节点）。
  useEffect(() => {
    void language // 显式依赖：回调内 t() 经全局语言读取，切换语言时触发本 effect
    const engine = engineRef.current!
    engine.invalidateForLocale()
    const store = useEditorStore.getState()
    const results = useValidationStore.getState()
    const engineNodes = toEngineNodes(store.nodes)
    const scopeNodes = engineNodes.map(toScopeNode)
    const edges = store.edges as ScopeEdgeLike[]
    const variables = store.variables
    const dataById = new Map<string, EngineNodeData>(
      engineNodes.map((node) => [node.id, node.data]),
    )
    const allIds = store.nodes.map((node) => node.id)
    const l1Ids = engine.runL1(engineNodes, allIds)
    const l2Ids = engine.runL2(
      scopeNodes,
      edges,
      variables,
      dataById,
      allIds,
      schemasRef.current,
      inputSchemasRef.current,
      cardBindingsRef.current,
    )
    const changed = new Set<string>([...l1Ids, ...l2Ids])
    if (changed.size > 0) {
      results.patchNodes(
        Object.fromEntries([...changed].map((id) => [id, engine.getNodeDiagnostics(id)])),
        store.dirty.revision,
      )
    }
    // L3 同样走 Worker，语言切换不让主线程多算一遍图级校验。
    const sig = graphDataSignature(scopeNodes, edges, variables)
    void validateL3WithFallback(
      getL3Worker(),
      scopeNodes,
      edges,
      () => engine.runGraph(scopeNodes, edges, variables),
    ).then((graphDiagnostics) => {
      const latest = useEditorStore.getState()
      const latestScopeNodes = toEngineNodes(latest.nodes).map(toScopeNode)
      const latestEdges = latest.edges as ScopeEdgeLike[]
      if (graphDataSignature(latestScopeNodes, latestEdges, latest.variables) !== sig) return
      engine.setGraphResult(sig, graphDiagnostics)
      const nodeOrder = topologicalOrder(latestScopeNodes, latestEdges)
      useValidationStore
        .getState()
        .setGraph(graphDiagnostics, nodeOrder, latest.dirty.revision)
    }).catch(() => {
      // Worker 与同步回退都失败：保留上一轮图诊断，等下次语言切换/编辑重试。
    })
  }, [language])
}
