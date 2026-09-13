import { ReactFlow, Background, Controls, MarkerType } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEditorStore } from '../../store/editorStore'

const edgeColor = '#1677ff'

export function FlowCanvas() {
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const onNodesChange = useEditorStore((state) => state.onNodesChange)
  const onEdgesChange = useEditorStore((state) => state.onEdgesChange)
  const onConnect = useEditorStore((state) => state.onConnect)
  const selectNode = useEditorStore((state) => state.selectNode)

  return (
    <div className="canvas-panel">
      <ReactFlow
        nodes={nodes}
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
