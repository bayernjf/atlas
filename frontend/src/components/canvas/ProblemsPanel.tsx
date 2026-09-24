/**
 * Problems 面板（M4 批 2 ⑧ / U41③，04 §6.5 末扩展条）。
 *
 * 全图 Diagnostic（节点 L1/L2 + 图级 L3）经 rank 排序后聚合展示；
 * 点击条目：nodeId → 选中并居中画布节点；pointer → 同步滚动定位到右侧属性面板对应字段并闪烁。
 * 必须渲染在 ReactFlowProvider 内（FlowCanvasInner）以使用 useReactFlow。
 */
import { useMemo, useState } from 'react'
import { useReactFlow } from '@xyflow/react'
import { Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'
import { useProblems } from '../../store/validationStore'
import type { Diagnostic } from '../../lib/validation/diagnostics'
import { useTranslation } from '../../locales'

const FLASH_CLASS = 'problems-field-flash'
const FLASH_MS = 1600
const FLASH_RETRY_MS = 1200
const FLASH_STEP_MS = 80

/**
 * `.side-card` 是节点面板/变量面板/属性面板共用的类名，取首个会命中节点面板，
 * 而 `[data-pointer]` 锚点只出现在属性面板的表单里——故必须逐面板查找。
 */
function findFieldAnchor(pointer: string): Element | null {
  const escaped = CSS.escape(pointer)
  const exactSelector = `[data-pointer="${escaped}"]`
  const prefixSelector = `[data-pointer^="${escaped}/"]`
  for (const panel of document.querySelectorAll('.side-card')) {
    const exact = panel.querySelector(exactSelector)
    if (exact) return exact
    const prefix = panel.querySelector(prefixSelector)
    if (prefix) return prefix
  }
  return null
}

/**
 * 定位并闪烁目标字段。属性面板要先响应 selectNode 才渲染出字段，一帧不够——
 * 原来固定等 60ms 查一次、查不到就静默放弃（点相邻节点的条目时经常不亮）。
 * 改为有界重试：最多 FLASH_RETRY_MS 内每 FLASH_STEP_MS 再查一次。
 */
function focusPropertyField(pointer: string, startedAt: number = Date.now()): void {
  const target = findFieldAnchor(pointer)
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    target.classList.remove(FLASH_CLASS)
    // 强制重排以重启动画
    void (target as HTMLElement).offsetWidth
    target.classList.add(FLASH_CLASS)
    window.setTimeout(() => target.classList.remove(FLASH_CLASS), FLASH_MS)
    return
  }
  if (Date.now() - startedAt >= FLASH_RETRY_MS) return
  window.setTimeout(() => focusPropertyField(pointer, startedAt), FLASH_STEP_MS)
}

export function ProblemsPanel() {
  const { t } = useTranslation('editor')
  const problems = useProblems()
  const [collapsed, setCollapsed] = useState(false)
  const { setCenter } = useReactFlow()
  const nodes = useEditorStore((state) => state.nodes)
  const selectNode = useEditorStore((state) => state.selectNode)
  const applyQuickFix = useEditorStore((state) => state.applyQuickFix)

  const labelById = useMemo(() => {
    const map = new Map<string, string>()
    for (const node of nodes) map.set(node.id, node.data.label || node.id)
    return map
  }, [nodes])

  const errorCount = problems.filter((problem) => problem.severity === 'error').length
  const warningCount = problems.length - errorCount

  const onSelect = (problem: Diagnostic) => {
    const nodeId = problem.loc.nodeId
    if (!nodeId) return
    selectNode(nodeId)
    const node = useEditorStore.getState().nodes.find((item) => item.id === nodeId)
    if (node) {
      const width = node.measured?.width ?? 180
      const height = node.measured?.height ?? 80
      void setCenter(node.position.x + width / 2, node.position.y + height / 2, {
        zoom: 1.1,
        duration: 300,
      })
    }
    if (problem.loc.pointer) focusPropertyField(problem.loc.pointer)
  }

  return (
    <div className={`problems-panel ${problems.length === 0 ? 'is-clean' : ''}`}>
      <button
        type="button"
        className="problems-header"
        onClick={() => setCollapsed((value) => !value)}
        aria-expanded={!collapsed}
      >
        <span className="problems-title">{t('problems.title')}</span>
        <span className="problems-counts">
          {errorCount > 0 && <span className="problems-count problems-count-error">{t('problems.errors', { count: errorCount })}</span>}
          {warningCount > 0 && <span className="problems-count problems-count-warning">{t('problems.warnings', { count: warningCount })}</span>}
          {problems.length === 0 && <span className="problems-count problems-count-ok">{t('problems.allClear')}</span>}
        </span>
        <span className="problems-chevron">{collapsed ? '▲' : '▼'}</span>
      </button>
      {!collapsed && problems.length > 0 && (
        <ul className="problems-list">
          {problems.map((problem, index) => {
            const nodeId = problem.loc.nodeId
            const clickable = !!nodeId
            return (
              <li
                // rank 后同序列稳定，index 作 key 可接受（无重排编辑）。
                key={`${problem.code}-${nodeId ?? 'graph'}-${problem.loc.pointer ?? ''}-${index}`}
                className={`problems-item problems-item-${problem.severity} ${clickable ? 'is-clickable' : ''}`}
                onClick={() => onSelect(problem)}
                title={clickable ? t('problems.clickLocate') : problem.message}
              >
                <span className="problems-item-icon">{problem.severity === 'error' ? '✕' : '!'}</span>
                <span className="problems-item-body">
                  <Typography.Text className="problems-item-message" ellipsis>
                    {problem.message}
                  </Typography.Text>
                  <span className="problems-item-meta">
                    {nodeId && <span className="problems-item-node">{labelById.get(nodeId) ?? nodeId}</span>}
                    {problem.loc.pointer && <span className="problems-item-pointer">{problem.loc.pointer}</span>}
                    {!nodeId && <span className="problems-item-node">{t('problems.wholeGraph')}</span>}
                  </span>
                </span>
                {problem.quickFix && problem.quickFix.length > 0 && nodeId && problem.loc.pointer && (
                  <button
                    type="button"
                    className="problems-fix-btn"
                    onClick={(event) => {
                      event.stopPropagation()
                      applyQuickFix(nodeId, problem.loc.pointer as string, problem.loc.token)
                    }}
                  >
                    {problem.quickFix[0].title}
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
