import { Handle, Position, type NodeProps } from '@xyflow/react'
import { NODE_CATALOG, validateNode, type EditorNodeData } from '../../lib/nodeCatalog'
import type { EditorNode } from '../../store/editorStore'

export function AtlasNode({ data, selected }: NodeProps<EditorNode>) {
  const meta = NODE_CATALOG[data.kind]
  const errors = validateNode(data)
  const invalid = errors.length > 0

  return (
    <div
      className={`atlas-node ${selected ? 'atlas-node-selected' : ''} atlas-node-${data.status}`}
      style={{ borderColor: meta.color }}
    >
      <Handle type="target" position={Position.Left} />
      <div className="atlas-node-header" style={{ backgroundColor: meta.color }}>
        <span>{meta.label}</span>
        {data.status === 'running' && <span className="atlas-node-status">运行中…</span>}
        {data.status === 'completed' && <span className="atlas-node-status">✓</span>}
        {invalid && (
          <span className="atlas-node-error-icon" title={errors.join('；')}>
            !
          </span>
        )}
      </div>
      <div className="atlas-node-label">{data.label}</div>
      {data.kind === 'condition' ? (
        <ConditionHandles data={data} />
      ) : data.kind === 'loop' ? (
        <LoopHandles />
      ) : data.kind === 'parallel' ? (
        <ParallelHandles data={data} />
      ) : (
        <Handle type="source" position={Position.Right} />
      )}
    </div>
  )
}

function LoopHandles() {
  const items = [
    { id: 'body', label: '循环体' },
    { id: 'exit', label: '退出' },
  ]
  return (
    <div className="atlas-node-branches">
      {items.map((item) => (
        <div key={item.id} className="atlas-node-branch">
          <span className="atlas-node-branch-label">{item.label}</span>
          <Handle id={item.id} type="source" position={Position.Right} title={item.label} />
        </div>
      ))}
    </div>
  )
}

function ParallelHandles({ data }: { data: EditorNodeData }) {
  const branches = data.config.branches ?? []
  return (
    <div className="atlas-node-branches">
      {branches.map((branch, index) => {
        const label = branch.label || `分支 ${index + 1}`
        return (
          <div key={`b${index}`} className="atlas-node-branch">
            <span className="atlas-node-branch-label">{label}</span>
            <Handle id={`b${index}`} type="source" position={Position.Right} title={label} />
          </div>
        )
      })}
    </div>
  )
}

function ConditionHandles({ data }: { data: EditorNodeData }) {  const branches = data.config.branches ?? []
  const items = [
    ...branches.map((branch, index) => ({ id: `b${index}`, label: branch.label || `分支 ${index + 1}` })),
    { id: 'default', label: '默认' },
  ]
  return (
    <div className="atlas-node-branches">
      {items.map((item) => (
        <div key={item.id} className="atlas-node-branch">
          <span className="atlas-node-branch-label">{item.label}</span>
          <Handle id={item.id} type="source" position={Position.Right} title={item.label} />
        </div>
      ))}
    </div>
  )
}

export type { EditorNodeData }
