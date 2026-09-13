import type { Connection, Edge, Node, NodeChange, EdgeChange } from '@xyflow/react'
import { applyEdgeChanges, applyNodeChanges, addEdge } from '@xyflow/react'
import { create } from 'zustand'

export type NodeKind = 'trigger' | 'decision' | 'action'

export type EditorNodeData = {
  label: string
  kind: NodeKind
  status: 'idle' | 'running' | 'completed'
}

export type EditorNode = Node<EditorNodeData>

type EditorState = {
  nodes: EditorNode[]
  edges: Edge[]
  selectedNodeId: string | null
  logs: string[]
  addNode: (kind: NodeKind) => void
  selectNode: (nodeId: string | null) => void
  updateSelectedLabel: (label: string) => void
  onNodesChange: (changes: NodeChange<EditorNode>[]) => void
  onEdgesChange: (changes: EdgeChange<Edge>[]) => void
  onConnect: (connection: Connection) => void
}

const nodeCatalog: Record<NodeKind, { label: string }> = {
  trigger: { label: '触发器' },
  decision: { label: 'AI 决策' },
  action: { label: '工具调用' },
}

const initialNodes: EditorNode[] = [
  {
    id: 'trigger-1',
    type: 'default',
    position: { x: 80, y: 180 },
    data: { label: '触发：新审批单', kind: 'trigger', status: 'idle' },
  },
  {
    id: 'decision-1',
    type: 'default',
    position: { x: 340, y: 180 },
    data: { label: 'AI 决策：是否通过', kind: 'decision', status: 'idle' },
  },
  {
    id: 'action-1',
    type: 'default',
    position: { x: 620, y: 180 },
    data: { label: '工具：提交审批结果', kind: 'action', status: 'idle' },
  },
]

const initialEdges: Edge[] = [
  { id: 'e-trigger-decision', source: 'trigger-1', target: 'decision-1' },
  { id: 'e-decision-action', source: 'decision-1', target: 'action-1' },
]

function nextPosition(count: number) {
  return { x: 100 + (count % 3) * 60, y: 80 + count * 70 }
}

export const useEditorStore = create<EditorState>((set, get) => ({
  nodes: initialNodes,
  edges: initialEdges,
  selectedNodeId: null,
  logs: ['编辑器骨架已加载：触发 → AI 决策 → 工具调用'],

  addNode: (kind) => {
    const count = get().nodes.length
    const id = `${kind}-${Date.now()}`
    const node: EditorNode = {
      id,
      position: nextPosition(count),
      data: { label: nodeCatalog[kind].label, kind, status: 'idle' },
    }
    set((state) => ({
      nodes: [...state.nodes, node],
      selectedNodeId: id,
      logs: [...state.logs, `添加节点：${nodeCatalog[kind].label}`],
    }))
  },

  selectNode: (nodeId) => set({ selectedNodeId: nodeId }),

  updateSelectedLabel: (label) => {
    const selectedId = get().selectedNodeId
    if (!selectedId) return
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === selectedId ? { ...node, data: { ...node.data, label } } : node,
      ),
      logs: [...state.logs, `更新节点 ${selectedId}：${label}`],
    }))
  },

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
}))
