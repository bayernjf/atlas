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

export type EditorNode = Node<EditorNodeData>
export type { NodeKind }

type EditorState = {
  nodes: EditorNode[]
  edges: Edge[]
  variables: GraphVariable[]
  selectedNodeId: string | null
  logs: string[]
  addNodeAt: (kind: NodeKind, position: { x: number; y: number }) => void
  selectNode: (nodeId: string | null) => void
  updateSelectedNode: (patch: Partial<EditorNodeData>) => void
  updateSelectedConfig: (patch: Partial<NodeConfig>) => void
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
    }))
  },

  updateSelectedConfig: (patch) => {
    const selectedId = get().selectedNodeId
    if (!selectedId) return
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === selectedId
          ? { ...node, data: { ...node.data, config: { ...node.data.config, ...patch } } }
          : node,
      ),
    }))
  },

  deleteSelectedNode: () => {
    const selectedId = get().selectedNodeId
    if (!selectedId) return
    set((state) => ({
      nodes: state.nodes
        .filter((node) => node.id !== selectedId)
        .map((node) => {
          if (node.data.kind !== 'condition') return node
          const config = node.data.config
          return {
            ...node,
            data: {
              ...node.data,
              config: {
                ...config,
                branches: config.branches?.map((branch) => ({
                  ...branch,
                  target: branch.target === selectedId ? '' : branch.target,
                })),
                defaultTarget: config.defaultTarget === selectedId ? '' : config.defaultTarget,
              },
            },
          }
        }),
      edges: state.edges.filter(
        (edge) => edge.source !== selectedId && edge.target !== selectedId,
      ),
      selectedNodeId: null,
      logs: [...state.logs, `删除节点：${selectedId}`],
    }))
  },

  addVariable: (variable) =>
    set((state) => ({
      variables: [...state.variables, variable],
      logs: [...state.logs, `新增全局变量：${variable.name}`],
    })),

  removeVariable: (name) =>
    set((state) => ({
      variables: state.variables.filter((variable) => variable.name !== name),
      logs: [...state.logs, `删除全局变量：${name}`],
    })),

  onNodesChange: (changes) => {
    set((state) => ({ nodes: applyNodeChanges(changes, state.nodes) }))
  },

  onEdgesChange: (changes) => {
    set((state) => ({ edges: applyEdgeChanges(changes, state.edges) }))
  },

  onConnect: (connection) => {
    set((state) => ({
      edges: addEdge({ ...connection }, state.edges),
      logs: [...state.logs, `连接节点：${connection.source} → ${connection.target}`],
    }))
  },

  loadGraph: (graph) => {
    const { nodes, edges, variables } = deserializeGraph(graph)
    set({ nodes: nodes as EditorNode[], edges, variables, selectedNodeId: null, logs: [`已加载 NL 生成草稿：${nodes.length} 个节点`] })
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
}))
