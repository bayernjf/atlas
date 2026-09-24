/**
 * 校验结果 store（M4 批 2 ⑦⑧）：分层引擎的产出在此汇聚，供
 * AtlasNode 角标、PropertyPanel 红字与 Problems 面板订阅。
 * 与编辑态 editorStore 分开：校验结果是编辑态的派生缓存，独立更新避免全画布重渲染。
 */
import { useMemo, useSyncExternalStore } from 'react'
import { create } from 'zustand'
import { getLanguage, subscribe, type Locale } from '../locales'
import { compileIssueMessage, type CompileIssue } from '../lib/apiClient'
import type { Diagnostic } from '../lib/validation/diagnostics'
import { dedupeServerDiagnostics, rank } from '../lib/validation/diagnostics'

type ValidationResultState = {
  /** 节点级诊断（L1+L2 合并），键＝节点 id。 */
  nodeDiagnostics: Record<string, Diagnostic[]>
  /** 图级诊断（L3：不可达/非法环）。 */
  graphDiagnostics: Diagnostic[]
  /** 最近一次 L3 使用的拓扑序（rank 用；成环节点末尾补入）。 */
  nodeOrder: string[]
  /** 已消费到的 editor dirty.revision。 */
  computedRevision: number
  /** 后端编译 422 的逐条诊断快照（docs/61 §2）；缺定位信息的图级条目也在此。 */
  serverIssues: CompileIssue[]
  /** 写入快照时的 computedRevision；引擎一旦重算即视为图已变更、快照陈旧。 */
  serverIssuesRevision: number
  patchNodes: (entries: Record<string, Diagnostic[]>, revision: number) => void
  setGraph: (graphDiagnostics: Diagnostic[], nodeOrder: string[], revision: number) => void
  pruneNodes: (aliveIds: string[]) => void
  setServerIssues: (issues: CompileIssue[]) => void
  clearServerIssues: () => void
  reset: () => void
}

const EMPTY: Pick<
  ValidationResultState,
  | 'nodeDiagnostics'
  | 'graphDiagnostics'
  | 'nodeOrder'
  | 'computedRevision'
  | 'serverIssues'
  | 'serverIssuesRevision'
> = {
  nodeDiagnostics: {},
  graphDiagnostics: [],
  nodeOrder: [],
  computedRevision: -1,
  serverIssues: [],
  serverIssuesRevision: -1,
}

export const useValidationStore = create<ValidationResultState>((set, get) => ({
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
  setServerIssues: (issues) =>
    set({ serverIssues: issues, serverIssuesRevision: get().computedRevision }),
  clearServerIssues: () => set({ serverIssues: [], serverIssuesRevision: -1 }),
  reset: () => set({ ...EMPTY }),
}))

/** 稳定空数组引用：selector 每次返回新 [] 会触发 useSyncExternalStore 无限重渲染。 */
const EMPTY_DIAGNOSTICS: Diagnostic[] = []

/** 单节点诊断订阅（AtlasNode 角标/PropertyPanel）。 */
export function useNodeDiagnostics(nodeId: string): Diagnostic[] {
  return useValidationStore((state) => state.nodeDiagnostics[nodeId] ?? EMPTY_DIAGNOSTICS)
}

/**
 * 全图 Problems 列表：本地节点级 + 图级统一经 rank（error 优先 → 拓扑序 → pointer →
 * token），再并入后端编译 422 快照（docs/61 §2）。快照按 (nodeId, pointer, code)
 * 三元组去重、本地优先；引擎一旦重算（图已变更）即视快照陈旧、不再展示，
 * 免得修完引用未重新编译时残留误导性的后端诊断。语言在渲染期解析，切语言自动重算。
 */
export function useProblems(): Diagnostic[] {
  const nodeDiagnostics = useValidationStore((state) => state.nodeDiagnostics)
  const graphDiagnostics = useValidationStore((state) => state.graphDiagnostics)
  const nodeOrder = useValidationStore((state) => state.nodeOrder)
  const serverIssues = useValidationStore((state) => state.serverIssues)
  const serverIssuesRevision = useValidationStore((state) => state.serverIssuesRevision)
  const computedRevision = useValidationStore((state) => state.computedRevision)
  const language = useSyncExternalStore(subscribe, getLanguage)
  return useMemo(() => {
    const local: Diagnostic[] = [...Object.values(nodeDiagnostics).flat(), ...graphDiagnostics]
    if (serverIssues.length === 0 || serverIssuesRevision !== computedRevision) {
      return rank(local, nodeOrder)
    }
    const server = serverIssues.map((issue) => toServerDiagnostic(issue, language))
    return rank(dedupeServerDiagnostics(local, server), nodeOrder)
  }, [
    nodeDiagnostics,
    graphDiagnostics,
    nodeOrder,
    serverIssues,
    serverIssuesRevision,
    computedRevision,
    language,
  ])
}

/** 后端编译诊断 → 共用 Diagnostic 形状；无 code 时按条目下标造合成本地码，不参与真实去重。 */
function toServerDiagnostic(issue: CompileIssue, language: Locale): Diagnostic {
  return {
    severity: 'error',
    layer: 'server',
    code: issue.code ?? `SERVER_COMPILE_${issue.index}`,
    message: compileIssueMessage(issue, language),
    loc: {
      ...(issue.nodeId ? { nodeId: issue.nodeId } : {}),
      ...(issue.pointer ? { pointer: issue.pointer } : {}),
    },
  }
}
