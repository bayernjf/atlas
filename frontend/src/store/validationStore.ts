/**
 * 校验结果 store（M4 批 2 ⑦⑧）：分层引擎的产出在此汇聚，供
 * AtlasNode 角标、PropertyPanel 红字与 Problems 面板订阅。
 * 与编辑态 editorStore 分开：校验结果是编辑态的派生缓存，独立更新避免全画布重渲染。
 */
import { useMemo } from 'react'
import { create } from 'zustand'
import type { Diagnostic } from '../lib/validation/diagnostics'
import { rank } from '../lib/validation/diagnostics'

type ValidationResultState = {
  /** 节点级诊断（L1+L2 合并），键＝节点 id。 */
  nodeDiagnostics: Record<string, Diagnostic[]>
  /** 图级诊断（L3：不可达/非法环）。 */
  graphDiagnostics: Diagnostic[]
  /** 最近一次 L3 使用的拓扑序（rank 用；成环节点末尾补入）。 */
  nodeOrder: string[]
  /** 已消费到的 editor dirty.revision。 */
  computedRevision: number
  patchNodes: (entries: Record<string, Diagnostic[]>, revision: number) => void
  setGraph: (graphDiagnostics: Diagnostic[], nodeOrder: string[], revision: number) => void
  pruneNodes: (aliveIds: string[]) => void
  reset: () => void
}

const EMPTY: Pick<
  ValidationResultState,
  'nodeDiagnostics' | 'graphDiagnostics' | 'nodeOrder' | 'computedRevision'
> = {
  nodeDiagnostics: {},
  graphDiagnostics: [],
  nodeOrder: [],
  computedRevision: -1,
}

export const useValidationStore = create<ValidationResultState>((set) => ({
  ...EMPTY,
  patchNodes: (entries, revision) =>
    set((state) => ({
      nodeDiagnostics: { ...state.nodeDiagnostics, ...entries },
      computedRevision: Math.max(state.computedRevision, revision),
    })),
  setGraph: (graphDiagnostics, nodeOrder, revision) =>
    set((state) => ({
      graphDiagnostics,
      nodeOrder,
      computedRevision: Math.max(state.computedRevision, revision),
    })),
  pruneNodes: (aliveIds) =>
    set((state) => {
      const alive = new Set(aliveIds)
      const nodeDiagnostics = Object.fromEntries(
        Object.entries(state.nodeDiagnostics).filter(([id]) => alive.has(id)),
      )
      return { nodeDiagnostics }
    }),
  reset: () => set({ ...EMPTY }),
}))

/** 稳定空数组引用：selector 每次返回新 [] 会触发 useSyncExternalStore 无限重渲染。 */
const EMPTY_DIAGNOSTICS: Diagnostic[] = []

/** 单节点诊断订阅（AtlasNode 角标/PropertyPanel）。 */
export function useNodeDiagnostics(nodeId: string): Diagnostic[] {
  return useValidationStore((state) => state.nodeDiagnostics[nodeId] ?? EMPTY_DIAGNOSTICS)
}

/** 全图 Problems 列表：节点级 + 图级统一经 rank（error 优先 → 拓扑序 → pointer → token）。 */
export function useProblems(): Diagnostic[] {
  const nodeDiagnostics = useValidationStore((state) => state.nodeDiagnostics)
  const graphDiagnostics = useValidationStore((state) => state.graphDiagnostics)
  const nodeOrder = useValidationStore((state) => state.nodeOrder)
  return useMemo(() => {
    const all: Diagnostic[] = [...Object.values(nodeDiagnostics).flat(), ...graphDiagnostics]
    return rank(all, nodeOrder)
  }, [nodeDiagnostics, graphDiagnostics, nodeOrder])
}
