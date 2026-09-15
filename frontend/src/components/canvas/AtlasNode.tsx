import { Handle, Position, type NodeProps } from '@xyflow/react'
import { NODE_CATALOG, validateNode, type EditorNodeData } from '../../lib/nodeCatalog'
import type { EditorNode } from '../../store/editorStore'
import { useEditorStore } from '../../store/editorStore'

export function AtlasNode({ id, data, selected }: NodeProps<EditorNode>) {
  const meta = NODE_CATALOG[data.kind]
  const errors = validateNode(data)
  const invalid = errors.length > 0
  const breakpoint = useEditorStore((state) => state.breakpoints[id])
  const toggleBreakpoint = useEditorStore((state) => state.toggleBreakpoint)

  return (
    <div
      className={`atlas-node ${selected ? 'atlas-node-selected' : ''} atlas-node-${data.status}`}
      style={{ borderColor: meta.color }}
    >
      <Handle type="target" position={Position.Left} />
      <button
        type="button"
        className={`atlas-node-breakpoint ${breakpoint ? 'is-active' : ''}`}
        title={
          breakpoint?.expression?.trim()
            ? `条件断点：${breakpoint.expression}`
            : breakpoint
              ? '节点断点（点击取消）'
              : '在此节点前设断点（调试运行）'
        }
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => {
          event.stopPropagation()
          toggleBreakpoint(id)
        }}
      >
        {breakpoint?.expression?.trim() ? <span className="atlas-node-breakpoint-dot" /> : null}
      </button>
      <div className="atlas-node-header" style={{ backgroundColor: meta.color }}>
        <span>{meta.label}</span>
        {data.status === 'running' && <span className="atlas-node-status">运行中…</span>}
        {data.status === 'paused' && <span className="atlas-node-status">已暂停</span>}
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
      ) : data.kind === 'human_approval' ? (
        <HumanApprovalHandles />
      ) : (
        <Handle type="source" position={Position.Right} />
      )}
    </div>
  )
}

function HumanApprovalHandles() {
  const items = [
    { id: 'approved', label: '通过' },
    { id: 'rejected', label: '拒绝' },
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
