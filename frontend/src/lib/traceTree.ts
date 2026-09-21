// docs/33 §4.2：Trace 时间线瀑布纯函数（不引 React/hook，组件 t() 负责把 kind key 翻成中文）。
import type { TraceSpanNode } from './apiClient'

export interface FlatSpan {
  span: TraceSpanNode
  depth: number
}

export interface LaidSpan extends FlatSpan {
  /** 横条左缘相对 root 起点的百分比（0–100）。 */
  leftPct: number
  /** 横条宽度百分比（最小 MIN_BAR_WIDTH_PCT 保证可见，且不越右界）。 */
  widthPct: number
}

/** 横条最小宽度（%），durationMs=0 的 span 也可见。 */
export const MIN_BAR_WIDTH_PCT = 0.5

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

/**
 * 深度优先把 span 树展平为行序列（行＝span + depth）。
 * collapseInternal=true 时折叠 internal span 整棵子树（subgraph 内部 span，对齐后端
 * to_dict(include_internal=False) 语义）；默认保留，组件以灰色区分。
 */
export function flattenSpans(
  root: TraceSpanNode | null | undefined,
  opts: { collapseInternal?: boolean } = {},
): FlatSpan[] {
  if (!root) return []
  const rows: FlatSpan[] = []
  const walk = (span: TraceSpanNode, depth: number): void => {
    if (opts.collapseInternal && span.internal) return
    rows.push({ span, depth })
    for (const child of span.children ?? []) {
      walk(child, depth + 1)
    }
  }
  walk(root, 0)
  return rows
}

/**
 * 以 root 的 startedAt/durationMs 为基准，给每行算 leftPct/widthPct。
 * root.durationMs=0（异常运行 root 未 finish）时以最大结束点兜底；仍为 0 则横条统一最小宽度。
 */
export function layoutWaterfall(rows: FlatSpan[], root: TraceSpanNode): LaidSpan[] {
  const base = Date.parse(root.startedAt)
  const baseValid = Number.isFinite(base)

  let total = root.durationMs > 0 ? root.durationMs : 0
  if (!(total > 0) && baseValid) {
    let maxEnd = 0
    for (const { span } of rows) {
      const start = Date.parse(span.startedAt) - base
      if (Number.isFinite(start)) {
        maxEnd = Math.max(maxEnd, Math.max(0, start) + Math.max(0, span.durationMs))
      }
    }
    total = maxEnd
  }

  return rows.map((row) => {
    const offset = baseValid ? Date.parse(row.span.startedAt) - base : 0
    const safeOffset = Number.isFinite(offset) ? Math.max(0, offset) : 0
    const leftPct = clamp(total > 0 ? (safeOffset / total) * 100 : 0, 0, 100)
    const rawWidth = total > 0 ? (Math.max(0, row.span.durationMs) / total) * 100 : MIN_BAR_WIDTH_PCT
    const widthPct = clamp(Math.max(rawWidth, MIN_BAR_WIDTH_PCT), 0, 100 - leftPct)
    return { ...row, leftPct, widthPct }
  })
}

/** kind → monitoring namespace i18n key（纯函数返 key，组件 t() 解析）。 */
const SPAN_KIND_LABEL_KEY: Record<string, string> = {
  run: 'trace.kind.run',
  node: 'trace.kind.node',
  tool: 'trace.kind.tool',
  parallel: 'trace.kind.parallel',
  subgraph: 'trace.kind.subgraph',
  task_dispatch: 'trace.kind.taskDispatch',
  task_done: 'trace.kind.taskDone',
  approval: 'trace.kind.approval',
}

/** 已知 kind 返 i18n key；未知 kind 原样返回（技术专名，t() 缺键时原样显示）。 */
export function spanKindLabelKey(kind: string): string {
  return SPAN_KIND_LABEL_KEY[kind] ?? kind
}

/** kind → AntD Tag color。 */
const SPAN_KIND_COLOR: Record<string, string> = {
  run: 'geekblue',
  node: 'blue',
  tool: 'green',
  parallel: 'purple',
  subgraph: 'cyan',
  task_dispatch: 'default',
  task_done: 'default',
  approval: 'orange',
}

export function spanKindColor(kind: string): string {
  return SPAN_KIND_COLOR[kind] ?? 'default'
}

/** 横条颜色：internal 灰、error 红、ok 蓝。 */
export function spanBarColor(span: TraceSpanNode): string {
  if (span.internal) return '#bfbfbf'
  return span.status === 'error' ? '#ff4d4f' : '#1677ff'
}

/** attrs 悬停文案；无 attrs 返 null（不挂 Tooltip）。 */
export function formatSpanAttrs(span: TraceSpanNode): string | null {
  if (!span.attrs || Object.keys(span.attrs).length === 0) return null
  return JSON.stringify(span.attrs, null, 2)
}
