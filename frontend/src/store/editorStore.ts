import type { Connection, Edge, Node, NodeChange, EdgeChange } from '@xyflow/react'
import { applyEdgeChanges, applyNodeChanges, addEdge } from '@xyflow/react'
import { create } from 'zustand'
import {
  defaultConfig,
  defaultRetry,
  NODE_CATALOG,
  type EditorNodeData,
  type NodeConfig,
  type NodeKind,
} from '../lib/nodeCatalog'
import type { GraphVariable } from '../lib/variables'
import { deserializeGraph, type SerializedGraph } from '../lib/graphSerializer'
import {
  INITIAL_DIRTY,
  markClean,
  markConfigEdit,
  markConsumed,
  markEdgeChanged,
  markGraphLoaded,
  markNodeAdded,
  markNodeDeleted,
  markNodeMetaEdit,
  markVariablesChanged,
  type ConsumedRanges,
  type ValidationDirty,
} from '../lib/validation/dirty'
import { buildReverseIndex, removeDanglingRef, renameNodeRefs } from '../lib/validation/reverseDeps'
import type { DiagnosticToken } from '../lib/validation/diagnostics'

export type EditorNode = Node<EditorNodeData>
export type { NodeKind }

// 会话级断点：键存在即启用；expression 非空为条件断点。不落 Graph JSON，刷新即失（04 §5.12）。
// B 包（docs/27 §4.2）：hitCount 每 N 次命中暂停；logMessage 非空即日志断点（只记日志不暂停）。
export type Breakpoint = { expression?: string; hitCount?: number; logMessage?: string }

type EditorState = {
  nodes: EditorNode[]
  edges: Edge[]
  variables: GraphVariable[]
  selectedNodeId: string | null
  logs: string[]
  breakpoints: Record<string, Breakpoint>
  /** NL 草稿的 paramWarnings（wire 仍是 string[]，见 04 §4.10）；加载新图/重新生成即刷新。 */
  nlWarnings: string[]
  /** M4 校验增量调度的失效范围（L1 节点字段 / L2 跨节点引用 / L3 全图结构）；批 2 调度器消费。 */
  dirty: ValidationDirty
  /** 调度器重算完对应层后清除失效标记（revision 不动）；不传 ranges 为全清。 */
  clearDirty: (ranges?: ConsumedRanges) => void
  addNodeAt: (kind: NodeKind, position: { x: number; y: number }) => void
  selectNode: (nodeId: string | null) => void
  updateSelectedNode: (patch: Partial<EditorNodeData>) => void
  updateSelectedConfig: (patch: Partial<NodeConfig>) => void
  /** 通用节点 config 更新（不限选中态；quickFix 与属性面板共用）。 */
  updateNodeConfig: (nodeId: string, patch: Partial<NodeConfig>) => void
  /** M4 批 3 ⑪ quickFix v1：删除悬空引用（L2 REF_NODE_NOT_FOUND 的唯一动作）。 */
  applyQuickFix: (nodeId: string, pointer: string, token?: DiagnosticToken) => void
  /** D30/B3 quickFix v2：重命名节点 id 并原子联动全部 target/模板引用、边、选中态与断点。 */
  renameNode: (oldId: string, rawNewId: string) => void
  deleteSelectedNode: () => void
  addVariable: (variable: GraphVariable) => void
  removeVariable: (name: string) => void
  onNodesChange: (changes: NodeChange<EditorNode>[]) => void
  onEdgesChange: (changes: EdgeChange<Edge>[]) => void
  onConnect: (connection: Connection) => void
  loadGraph: (graph: SerializedGraph) => void
  setNodeStatus: (nodeId: string, status: EditorNodeData['status']) => void
  resetRunStatuses: () => void
  appendLog: (message: string) => void
  toggleBreakpoint: (nodeId: string) => void
  setBreakpointExpression: (nodeId: string, expression: string) => void
  setBreakpointHitCount: (nodeId: string, hitCount: number | null) => void
  setBreakpointLogMessage: (nodeId: string, logMessage: string) => void
  clearBreakpoints: () => void
  setNlWarnings: (warnings: string[]) => void
}

export function nextId(kind: NodeKind, existing: EditorNode[]): string {
  const prefix = `${kind}-`
  let max = 0
  for (const node of existing) {
    if (node.id.startsWith(prefix)) {
      const suffix = Number(node.id.slice(prefix.length))
      if (Number.isInteger(suffix)) max = Math.max(max, suffix)
    }
  }
  return `${prefix}${max + 1}`
}

const NODE_ID_RE = /^[A-Za-z0-9_-]+$/

/**
 * 校验新节点 id（D30/B3 重命名联动）：非空、字符合法（字母数字 _-，不含 '.' 与空格，
 * 以免与模板路径段冲突）、非保留头 global、不与现有 id 重复。返回错误文案；合法（含无变化）返回 null。
 */
export function validateNodeId(rawNewId: string, oldId: string, allIds: string[]): string | null {
  const newId = rawNewId.trim()
  if (!newId) return '节点 ID 不能为空'
  if (newId === oldId) return null
  if (newId === 'global') return '节点 ID 不能使用保留字 global'
  if (!NODE_ID_RE.test(newId)) return '节点 ID 仅支持字母、数字、下划线、连字符'
  if (allIds.includes(newId)) return `节点 ID 已存在：${newId}`
  return null
}

const initialNodes: EditorNode[] = [
  {
    id: 'trigger-1',
    position: { x: 80, y: 180 },
    data: {
      label: '触发：新退款申请',
      kind: 'trigger',
      status: 'idle',
      description: '',
      config: { ...defaultConfig('trigger'), triggerType: 'webhook', webhookUrl: '/hooks/refund' },
      retry: defaultRetry(),
    },
  },
  {
    id: 'ai_decision-1',
    position: { x: 360, y: 180 },
    data: {
      label: 'AI 决策：退款还是人工',
      kind: 'ai_decision',
      status: 'idle',
      description: '',
      config: {
        ...defaultConfig('ai_decision'),
        promptTemplate:
          '退款单 {{trigger-1.context.payload.order_id}}：{{trigger-1.context.payload.reason}}，金额 {{trigger-1.context.payload.amount}}，审批限额 {{global.approval_limit}}',
      },
      retry: defaultRetry(),
    },
  },
  {
    id: 'tool_call-1',
    position: { x: 680, y: 180 },
    data: {
      label: '工具：执行退款或转人工',
      kind: 'tool_call',
      status: 'idle',
      description: '',
      config: { ...defaultConfig('tool_call'), tool: 'shop/process_refund' },
      retry: defaultRetry(),
    },
  },
]

const initialEdges: Edge[] = [
  { id: 'e-trigger-decision', source: 'trigger-1', target: 'ai_decision-1' },
  { id: 'e-decision-action', source: 'ai_decision-1', target: 'tool_call-1' },
]

const initialVariables: GraphVariable[] = [
  { name: 'approval_limit', type: 'number', value: '500', scope: 'global' },
]

export const useEditorStore = create<EditorState>((set, get) => ({
  nodes: initialNodes,
  edges: initialEdges,
  variables: initialVariables,
  selectedNodeId: null,
  logs: ['W9-W10 退款 Demo：选择退款单后「编译并运行」，节点实时高亮；也可用自然语言生成草稿'],
  breakpoints: {},
  nlWarnings: [],
  dirty: INITIAL_DIRTY,

  clearDirty: (ranges) =>
    set((state) => ({ dirty: ranges ? markConsumed(state.dirty, ranges) : markClean(state.dirty) })),

  addNodeAt: (kind, position) => {
    const id = nextId(kind, get().nodes)
    const node: EditorNode = {
      id,
      position,
      data: {
        label: NODE_CATALOG[kind].label,
        kind,
        status: 'idle',
        description: '',
        config: defaultConfig(kind),
        retry: defaultRetry(),
      },
    }
    set((state) => ({
      nodes: [...state.nodes, node],
      selectedNodeId: id,
      logs: [...state.logs, `添加节点：${NODE_CATALOG[kind].label}（${id}）`],
      dirty: markNodeAdded(state.dirty, id),
    }))
  },

  selectNode: (nodeId) => set({ selectedNodeId: nodeId }),

  updateSelectedNode: (patch) => {
    const selectedId = get().selectedNodeId
    if (!selectedId) return
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === selectedId ? { ...node, data: { ...node.data, ...patch } } : node,
      ),
      // label/描述等元信息只影响该节点 L1（名称必填）。
      dirty: markNodeMetaEdit(state.dirty, selectedId),
    }))
  },

  updateSelectedConfig: (patch) => {
    const selectedId = get().selectedNodeId
    if (!selectedId) return
    get().updateNodeConfig(selectedId, patch)
  },

  updateNodeConfig: (nodeId, patch) =>
    set((state) => {
      const target = state.nodes.find((node) => node.id === nodeId)
      if (!target) return {}
      return {
        nodes: state.nodes.map((node) =>
          node.id === nodeId
            ? { ...node, data: { ...node.data, config: { ...node.data.config, ...patch } } }
            : node,
        ),
        dirty: markConfigEdit(state.dirty, nodeId, patch as Record<string, unknown>, {
          kind: target.data.kind,
          allNodeIds: state.nodes.map((node) => node.id),
        }),
      }
    }),

  applyQuickFix: (nodeId, pointer, token) =>
    set((state) => {
      const node = state.nodes.find((item) => item.id === nodeId)
      if (!node) return {}
      const patch = removeDanglingRef(
        node.data.kind,
        node.data.config as Record<string, unknown>,
        pointer,
        token,
      )
      if (!patch) return {}
      return {
        nodes: state.nodes.map((item) =>
          item.id === nodeId
            ? { ...item, data: { ...item.data, config: { ...item.data.config, ...patch } } }
            : item,
        ),
        logs: [...state.logs, `删除悬空引用：${node.data.label || nodeId} ${pointer}`],
        dirty: markConfigEdit(state.dirty, nodeId, patch, {
          kind: node.data.kind,
          allNodeIds: state.nodes.map((item) => item.id),
        }),
      }
    }),

  renameNode: (oldId, rawNewId) =>
    set((state) => {
      const newId = rawNewId.trim()
      const allIds = state.nodes.map((node) => node.id)
      if (newId === oldId || validateNodeId(newId, oldId, allIds) !== null) return {}
      if (!state.nodes.some((node) => node.id === oldId)) return {}
      const scopeNodes = state.nodes.map((node) => ({
        id: node.id,
        kind: node.data.kind,
        config: node.data.config as Record<string, unknown>,
      }))
      const edits = renameNodeRefs(scopeNodes, oldId, newId)
      const nodes = state.nodes.map((node) => {
        if (node.id === oldId) return { ...node, id: newId }
        const edit = edits.get(node.id)
        if (!edit) return node
        return {
          ...node,
          data: { ...node.data, config: { ...node.data.config, ...edit } as NodeConfig },
        }
      })
      const edges = state.edges.map((edge) => ({
        ...edge,
        source: edge.source === oldId ? newId : edge.source,
        target: edge.target === oldId ? newId : edge.target,
      }))
      // 重命名改 id（结构）+ 跨节点引用（L2）：保守置 L3 并全量 L2（重命名极低频）。
      let dirty = markVariablesChanged(state.dirty, allIds)
      dirty = markEdgeChanged(dirty, [newId])
      return {
        nodes,
        edges,
        selectedNodeId: state.selectedNodeId === oldId ? newId : state.selectedNodeId,
        breakpoints: Object.fromEntries(
          Object.entries(state.breakpoints).map(([id, value]) => [id === oldId ? newId : id, value]),
        ),
        logs: [...state.logs, `重命名节点：${oldId} → ${newId}`],
        dirty,
      }
    }),

  deleteSelectedNode: () => {
    const selectedId = get().selectedNodeId
    if (!selectedId) return
    set((state) => {
      // M4 批 3 ⑩：reverseDeps 定位全部引用方，统一清 target 引用（替代按 kind 硬编码）。
      const scopeNodes = state.nodes.map((node) => ({
        id: node.id,
        kind: node.data.kind,
        config: node.data.config as Record<string, unknown>,
      }))
      const deps = buildReverseIndex(scopeNodes).referrersOf(selectedId)
      const targetDeps = new Map<string, Array<{ pointer: string }>>()
      for (const dep of deps) {
        if (dep.kind !== 'target') continue
        const list = targetDeps.get(dep.referrerId)
        if (list) list.push({ pointer: dep.pointer })
        else targetDeps.set(dep.referrerId, [{ pointer: dep.pointer }])
      }
      const referrerIds = [...new Set(deps.map((dep) => dep.referrerId))]
      const nodes = state.nodes
        .filter((node) => node.id !== selectedId)
        .map((node) => {
          const refs = targetDeps.get(node.id)
          if (!refs) return node
          let config = node.data.config
          for (const ref of refs) {
            const patch = removeDanglingRef(
              node.data.kind,
              config as Record<string, unknown>,
              ref.pointer,
            )
            if (patch) config = { ...config, ...patch } as NodeConfig
          }
          return { ...node, data: { ...node.data, config } }
        })
      return {
        nodes,
        edges: state.edges.filter(
          (edge) => edge.source !== selectedId && edge.target !== selectedId,
        ),
        selectedNodeId: null,
        breakpoints: Object.fromEntries(
          Object.entries(state.breakpoints).filter(([nodeId]) => nodeId !== selectedId),
        ),
        logs: [...state.logs, `删除节点：${selectedId}`],
        dirty: markNodeDeleted(
          state.dirty,
          nodes.map((node) => node.id),
          referrerIds,
        ),
      }
    })
  },

  addVariable: (variable) =>
    set((state) => ({
      variables: [...state.variables, variable],
      logs: [...state.logs, `新增全局变量：${variable.name}`],
      dirty: markVariablesChanged(
        state.dirty,
        state.nodes.map((node) => node.id),
      ),
    })),

  removeVariable: (name) =>
    set((state) => ({
      variables: state.variables.filter((variable) => variable.name !== name),
      logs: [...state.logs, `删除全局变量：${name}`],
      dirty: markVariablesChanged(
        state.dirty,
        state.nodes.map((node) => node.id),
      ),
    })),

  onNodesChange: (changes) => {
    set((state) => {
      const nodes = applyNodeChanges(changes, state.nodes)
      const removed = changes.some((change) => change.type === 'remove')
      return removed
        ? { nodes, dirty: markNodeDeleted(state.dirty, nodes.map((node) => node.id)) }
        : { nodes }
    })
  },

  onEdgesChange: (changes) => {
    set((state) => {
      const edges = applyEdgeChanges(changes, state.edges)
      // 删边改变可达性/作用域（位置/选择变更不影响校验）；批 1 无 reverseDeps，L2 全量保守。
      const removed = changes.some((change) => change.type === 'remove')
      return removed
        ? { edges, dirty: markEdgeChanged(state.dirty, state.nodes.map((node) => node.id)) }
        : { edges }
    })
  },

  onConnect: (connection) => {
    set((state) => ({
      edges: addEdge({ ...connection }, state.edges),
      logs: [...state.logs, `连接节点：${connection.source} → ${connection.target}`],
      // 新增连线可改变下游整片子图的可达作用域，批 1 无 reverseDeps，L2 全量保守（与删边一致）。
      dirty: markEdgeChanged(
        state.dirty,
        state.nodes.map((node) => node.id),
      ),
    }))
  },

  loadGraph: (graph) => {
    const { nodes, edges, variables } = deserializeGraph(graph)
    const loadedNodes = nodes as EditorNode[]
    set((state) => ({
      nodes: loadedNodes,
      edges,
      variables,
      selectedNodeId: null,
      breakpoints: {},
      // 换图即失效：NL 参数警告只对刚生成/加载的那张草稿有意义
      nlWarnings: [],
      logs: [`已加载 NL 生成草稿：${loadedNodes.length} 个节点`],
      dirty: markGraphLoaded(
        state.dirty,
        loadedNodes.map((node) => node.id),
      ),
    }))
  },

  setNodeStatus: (nodeId, status) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === nodeId ? { ...node, data: { ...node.data, status } } : node,
      ),
    }))
  },

  resetRunStatuses: () => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.data.status === 'idle' ? node : { ...node, data: { ...node.data, status: 'idle' as const } },
      ),
    }))
  },

  appendLog: (message) => set((state) => ({ logs: [...state.logs, message] })),

  toggleBreakpoint: (nodeId) =>
    set((state) => {
      const next = { ...state.breakpoints }
      if (nodeId in next) {
        delete next[nodeId]
      } else {
        next[nodeId] = {}
      }
      return { breakpoints: next }
    }),

  setBreakpointExpression: (nodeId, expression) =>
    set((state) => ({
      breakpoints: {
        ...state.breakpoints,
        [nodeId]: { ...state.breakpoints[nodeId], expression },
      },
    })),

  setBreakpointHitCount: (nodeId, hitCount) =>
    set((state) => ({
      breakpoints: {
        ...state.breakpoints,
        // null/<1 视为缺省（每次命中暂停），不向下传非法值。
        [nodeId]: {
          ...state.breakpoints[nodeId],
          hitCount: hitCount !== null && hitCount >= 1 ? hitCount : undefined,
        },
      },
    })),

  setBreakpointLogMessage: (nodeId, logMessage) =>
    set((state) => ({
      breakpoints: {
        ...state.breakpoints,
        // 空白串视为缺省（非空才是日志断点，与后端 strip 口径一致）。
        [nodeId]: {
          ...state.breakpoints[nodeId],
          logMessage: logMessage.trim() ? logMessage : undefined,
        },
      },
    })),

  clearBreakpoints: () => set({ breakpoints: {} }),

  setNlWarnings: (warnings) => set({ nlWarnings: warnings }),
}))
