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
        edges={edges.map((edge) => ({
          ...edge,
          markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
          style: { stroke: edgeColor },
        }))}
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
