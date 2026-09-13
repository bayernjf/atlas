import { Handle, Position, type NodeProps } from '@xyflow/react'
import { NODE_CATALOG, validateNode, type EditorNodeData } from '../../lib/nodeCatalog'
import type { EditorNode } from '../../store/editorStore'

export function AtlasNode({ data, selected }: NodeProps<EditorNode>) {
  const meta = NODE_CATALOG[data.kind]
  const errors = validateNode(data)
  const invalid = errors.length > 0

  return (
    <div
      className={`atlas-node ${selected ? 'atlas-node-selected' : ''}`}
      style={{ borderColor: meta.color }}
    >
      <Handle type="target" position={Position.Left} />
      <div className="atlas-node-header" style={{ backgroundColor: meta.color }}>
        <span>{meta.label}</span>
        {invalid && (
          <span className="atlas-node-error-icon" title={errors.join('；')}>
            !
          </span>
        )}
      </div>
      <div className="atlas-node-label">{data.label}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

export type { EditorNodeData }
