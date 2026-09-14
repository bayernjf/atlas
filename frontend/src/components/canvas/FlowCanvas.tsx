import { useCallback, useMemo } from 'react'
import { ReactFlow, Background, Controls, MarkerType, ReactFlowProvider, useReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEditorStore, type EditorNode } from '../../store/editorStore'
import type { NodeKind } from '../../lib/nodeCatalog'
import { token } from '../../theme/tokens'
import { AtlasNode } from './AtlasNode'

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
  const { screenToFlowPosition } = useReactFlow()

  const nodeTypes = useMemo(() => ({ atlasNode: AtlasNode }), [])

  const conditionEdgeLabels = useMemo(() => {
    const labels = new Map<string, string>()
    for (const node of nodes) {
      if (node.data.kind !== 'condition') continue
      for (const branch of node.data.config.branches ?? []) {
        if (branch.target) labels.set(`${node.id}->${branch.target}`, branch.label || branch.target)
      }
      if (node.data.config.defaultTarget) {
        labels.set(`${node.id}->${node.data.config.defaultTarget}`, '默认')
      }
    }
    return labels
  }, [nodes])

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
          const branchLabel = conditionEdgeLabels.get(`${edge.source}->${edge.target}`)
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
    </div>
  )
}
