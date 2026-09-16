/**
 * 适配器发现的共享缓存 + 拓扑作用域 React hook（04 §4.9 / §6.5）。
 *
 * ToolCallConfig 的工具选择与 PropertyPanel/画布节点的 L2 校验共用同一次
 * /api/adapters 请求；发现失败时不阻塞编辑（无 schema → 深层路径放行）。
 */
import { useMemo, useSyncExternalStore } from 'react'
import type { Edge } from '@xyflow/react'
import { listAdapters, type AdapterInfo } from './apiClient'
import { buildScopeIndex, type JsonSchema, type ScopeIndex } from './scope'
import type { GraphVariable } from './variables'
import type { EditorNode } from '../store/editorStore'

type AdapterSnapshot = { adapters: AdapterInfo[] | null; fetchFailed: boolean }

let snapshot: AdapterSnapshot = { adapters: null, fetchFailed: false }
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function loadAdapters(): Promise<void> {
  if (inflight || snapshot.adapters || snapshot.fetchFailed) {
    return inflight ?? Promise.resolve()
  }
  inflight = listAdapters()
    .then((items) => {
      snapshot = { adapters: items, fetchFailed: false }
    })
    .catch(() => {
      snapshot = { adapters: null, fetchFailed: true }
    })
    .finally(() => {
      inflight = null
      emit()
    })
  return inflight
}

export function useAdapters(): AdapterSnapshot {
  loadAdapters()
  return useSyncExternalStore(subscribe, () => snapshot, () => snapshot)
}

/** 以 `<adapter_id>/<tool>` 为键的输出 schema 表；发现未就绪/失败时为空表。 */
export function useToolOutputSchemas(): Record<string, JsonSchema> {
  const { adapters } = useAdapters()
  return useMemo(() => {
    const table: Record<string, JsonSchema> = {}
    for (const adapter of adapters ?? []) {
      for (const tool of adapter.tools) {
        table[`${adapter.id}/${tool.name}`] = tool.output_schema
      }
    }
    return table
  }, [adapters])
}

type ScopeInput = {
  nodes: EditorNode[]
  edges: Edge[]
  variables: Pick<GraphVariable, 'name'>[]
}

export function useScopeIndex({ nodes, edges, variables }: ScopeInput): ScopeIndex {
  const scopeNodes = useMemo(
    () => nodes.map((node) => ({ id: node.id, kind: node.data.kind, config: node.data.config })),
    [nodes],
  )
  return useMemo(
    () => buildScopeIndex(scopeNodes, edges, variables),
    [scopeNodes, edges, variables],
  )
}
