/**
 * M3 Schema 驱动表单的控件契约（04 §4.10 / 03 `form_renderer` / ADR T17）。
 *
 * WidgetProps 是 docs/19 §1.3.4 的 M3 子集：不含 uiSchema（UISchema 随 M4）。
 * 八件内置控件全部由既有 AntD 组件承载，零新依赖。
 */
import type { ReactNode } from 'react'
import type { MetaSchema } from '../schemas/metaSchema'
import type { Diagnostic } from '../validation/diagnostics'

export const BUILTIN_WIDGETS = [
  'text',
  'number',
  'select',
  'textarea',
  'switch',
  'json',
  'expression',
  'variable-input',
] as const

export type WidgetName = (typeof BUILTIN_WIDGETS)[number]

/** variable-input 补全用：复用 M0 ScopeIndex.listPathsAt 的结构子集。 */
export type WidgetScope = {
  listPathsAt(nodeId: string): string[]
}

export type WidgetProps = {
  /** 当前字段值（params 对象内的解析后值；json 控件可持有未解析草稿）。 */
  value: unknown
  /** 受控更新：接收下一整个字段值；对象/数组由 FormRenderer 不可变合并。 */
  onChange(next: unknown): void
  /** 当前字段 schema 片段（MetaSchema/Capability 白名单子集）。 */
  schema: MetaSchema
  /** 拓扑作用域；仅 variable-input 补全消费，其余控件忽略。 */
  scope?: WidgetScope
  /** 当前编辑节点 id，variable-input 调 scope.listPathsAt 用。 */
  nodeId?: string
  /** pointer 命中本字段的 M2 诊断（field/template 均可）。 */
  diagnostics?: Diagnostic[]
  placeholder?: string
  /** 多行控件行数（textarea/variable-input/json）。 */
  rows?: number
}

export type WidgetComponent = (props: WidgetProps) => ReactNode
