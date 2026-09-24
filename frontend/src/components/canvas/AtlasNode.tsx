import { Handle, Position, type NodeProps } from '@xyflow/react'
import { NODE_CATALOG, type EditorNodeData } from '../../lib/nodeCatalog'
import type { EditorNode } from '../../store/editorStore'
import { useEditorStore } from '../../store/editorStore'
import { useNodeDiagnostics } from '../../store/validationStore'
import { useTranslation } from '../../locales'

export function AtlasNode({ id, data, selected }: NodeProps<EditorNode>) {
  const { t } = useTranslation('editor')
  const meta = NODE_CATALOG[data.kind]
  // M4 批 2 ⑦：诊断由分层校验引擎统一产出（不再每节点各建一次 ScopeIndex）。
  const diagnostics = useNodeDiagnostics(id)
  const invalid = diagnostics.length > 0
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
            ? t('canvas.bp.conditional', { expression: breakpoint.expression })
            : breakpoint
              ? t('canvas.bp.node')
              : t('canvas.bp.set')
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
        {data.status === 'running' && <span className="atlas-node-status">{t('canvas.statusRunning')}</span>}
        {data.status === 'paused' && <span className="atlas-node-status">{t('canvas.statusPaused')}</span>}
        {data.status === 'completed' && <span className="atlas-node-status">✓</span>}
        {invalid && (
          <span className="atlas-node-error-icon" title={diagnostics.map((d) => d.message).join(t('canvas.diagSep'))}>
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
  const { t } = useTranslation('editor')
  const items = [
    { id: 'approved', label: t('canvas.approvalApproved') },
    { id: 'rejected', label: t('canvas.approvalRejected') },
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
  const { t } = useTranslation('editor')
  const items = [
    { id: 'body', label: t('canvas.loopBody') },
    { id: 'exit', label: t('canvas.loopExit') },
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
  const { t } = useTranslation('editor')
  const branches = data.config.branches ?? []
  return (
    <div className="atlas-node-branches">
      {branches.map((branch, index) => {
        const label = branch.label || t('canvas.branchFallback', { n: index + 1 })
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

function ConditionHandles({ data }: { data: EditorNodeData }) {
  const { t } = useTranslation('editor')
  const branches = data.config.branches ?? []
  const items = [
    ...branches.map((branch, index) => ({ id: `b${index}`, label: branch.label || t('canvas.branchFallback', { n: index + 1 }) })),
    { id: 'default', label: t('canvas.branchDefault') },
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
