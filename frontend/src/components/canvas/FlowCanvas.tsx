import { useCallback, useEffect, useMemo } from 'react'
import { ReactFlow, Background, Controls, MarkerType, ReactFlowProvider, useReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEditorStore, type EditorNode } from '../../store/editorStore'
import type { NodeKind } from '../../lib/nodeCatalog'
import { token } from '../../theme/tokens'
import { useTranslation } from '../../locales'
import { AtlasNode } from './AtlasNode'
import { ProblemsPanel } from './ProblemsPanel'

const edgeColor = token('color-primary')
const DND_MIME = 'application/atlas-node'

export function FlowCanvas() {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner />
    </ReactFlowProvider>
  )
}
function FlowCanvasInner() {
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const onNodesChange = useEditorStore((state) => state.onNodesChange)
  const onEdgesChange = useEditorStore((state) => state.onEdgesChange)
  const onConnect = useEditorStore((state) => state.onConnect)
  const selectNode = useEditorStore((state) => state.selectNode)
  const addNodeAt = useEditorStore((state) => state.addNodeAt)
  // 打包 ZU（docs/94 E-6）：反思「去修改」节点级定位——provider 内消费 pending 节点并居中。
  const pendingFocusNodeId = useEditorStore((state) => state.pendingFocusNodeId)
  const clearFocusNode = useEditorStore((state) => state.clearFocusNode)
  const { screenToFlowPosition, setCenter } = useReactFlow()
  const { t } = useTranslation('editor')

  const nodeTypes = useMemo(() => ({ atlasNode: AtlasNode }), [])

  // 待聚焦节点：选中并居中（measured 缺省 180×80、zoom 1.1、duration 300，照 ProblemsPanel）。
  // 图已加载但找不到节点时只打开图、清除请求、不报错（契约 E-6）；节点未就绪则随 nodes 变化重试。
  useEffect(() => {
    if (!pendingFocusNodeId) return
    const current = useEditorStore.getState().nodes
    const node = current.find((item) => item.id === pendingFocusNodeId)
    if (node) {
      selectNode(node.id)
      const width = node.measured?.width ?? 180
      const height = node.measured?.height ?? 80
      void setCenter(node.position.x + width / 2, node.position.y + height / 2, {
        zoom: 1.1,
        duration: 300,
      })
      clearFocusNode()
    } else if (current.length > 0) {
      clearFocusNode()
    }
  }, [pendingFocusNodeId, nodes, selectNode, setCenter, clearFocusNode])

  const routingEdgeLabels = useMemo(() => {
    const labels = new Map<string, string>()
    for (const node of nodes) {
      if (node.data.kind === 'condition') {
        for (const branch of node.data.config.branches ?? []) {
          if (branch.target) labels.set(`${node.id}->${branch.target}`, branch.label || branch.target)
        }
        if (node.data.config.defaultTarget) {
          labels.set(`${node.id}->${node.data.config.defaultTarget}`, t('canvas.branchDefault'))
        }
      } else if (node.data.kind === 'loop') {
        if (node.data.config.bodyTarget) {
          labels.set(`${node.id}->${node.data.config.bodyTarget}`, t('canvas.loopBody'))
        }
        if (node.data.config.exitTarget) {
          labels.set(`${node.id}->${node.data.config.exitTarget}`, t('canvas.loopExit'))
        }
      } else if (node.data.kind === 'parallel') {
        for (const branch of node.data.config.branches ?? []) {
          if (branch.target) labels.set(`${node.id}->${branch.target}`, branch.label || branch.target)
        }
      } else if (node.data.kind === 'human_approval') {
        if (node.data.config.approvedTarget) {
          labels.set(`${node.id}->${node.data.config.approvedTarget}`, t('canvas.approvalApproved'))
        }
        if (node.data.config.rejectedTarget) {
          labels.set(`${node.id}->${node.data.config.rejectedTarget}`, t('canvas.approvalRejected'))
        }
      }
    }
    return labels
  }, [nodes, t])

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      const kind = event.dataTransfer.getData(DND_MIME) as NodeKind
      if (!kind) return
      event.preventDefault()
      addNodeAt(kind, screenToFlowPosition({ x: event.clientX, y: event.clientY }))
    },
    [addNodeAt, screenToFlowPosition],
  )

  return (
    <div className="canvas-panel" onDragOver={onDragOver} onDrop={onDrop}>
      <ReactFlow
        nodes={nodes.map((node: EditorNode) => ({ ...node, type: 'atlasNode' }))}
        nodeTypes={nodeTypes}
        edges={edges.map((edge) => {
          const branchLabel = routingEdgeLabels.get(`${edge.source}->${edge.target}`)
          return {
            ...edge,
            label: branchLabel,
            labelBgPadding: [6, 2] as [number, number],
            labelBgBorderRadius: 4,
            labelStyle: { fontSize: 11, fill: edgeColor },
            markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
            style: { stroke: edgeColor },
          }
        })}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_, node) => selectNode(node.id)}
        onPaneClick={() => selectNode(null)}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>
      <ProblemsPanel />
    </div>
  )
}
