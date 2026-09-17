/**
 * M3 Schema 驱动表单的控件契约（04 §4.10 / 03 `form_renderer` / ADR T17）。
 *
 * WidgetProps 是 docs/19 §1.3.4 的 M3 子集：不含 uiSchema（UISchema 随 M4）。
 * 内置控件全部由既有 AntD 组件承载，零新依赖（M3 八件；M4 节点迁移补 radio 共九件）。
 */
import type { ReactNode } from 'react'
import type { MetaSchema } from '../schemas/metaSchema'
import type { Diagnostic, DiagnosticToken } from '../validation/diagnostics'

export const BUILTIN_WIDGETS = [
  'text',
  'number',
  'select',
  'radio',
  'textarea',
  'switch',
  'json',
  'expression',
  'variable-input',
] as const

export type WidgetName = (typeof BUILTIN_WIDGETS)[number]

/**
 * 节点业务控件名（M4）：连线目标选择，由 nodeWidgets.tsx 提供、nodeRegistry
 * 注册（不在内置九件内）；节点 schema 以 x-widget 引用，工具表单不注册、遇之降级 json。
 */
export const TARGET_SELECT_WIDGET = 'target-select'

/** 目标节点候选项（target-select 节点业务控件消费）。 */
export type WidgetTargetOption = { value: string; label: string }

/** variable-input 补全用：复用 M0 ScopeIndex.listPathsAt 的结构子集。 */
export type WidgetScope = {
  listPathsAt(nodeId: string): string[]
  /** M4 节点迁移：目标节点候选（连线 target 选择）；工具表单不提供，target-select 缺省降级为空。 */
  listNodeTargets?(): WidgetTargetOption[]
}

export type WidgetProps = {
  /** 当前字段值（params 对象内的解析后值；json 控件可持有未解析草稿）。 */
  value: unknown
  /** 受控更新：接收下一整个字段值；对象/数组由 FormRenderer 不可变合并。 */
  onChange(next: unknown): void
  /** 当前字段 schema 片段（MetaSchema/Capability 白名单子集）。 */
  schema: MetaSchema
  /** 拓扑作用域；variable-input 消费 listPathsAt、target-select 消费 listNodeTargets。 */
  scope?: WidgetScope
  /** 当前编辑节点 id，variable-input 调 scope.listPathsAt 用。 */
  nodeId?: string
  /** pointer 命中本字段的 M2 诊断（field/template 均可）。 */
  diagnostics?: Diagnostic[]
  /** 本字段诊断里的 token 区间投影（M2 renderMarkers）；模板控件据此做字段内高亮。 */
  markers?: DiagnosticToken[]
  placeholder?: string
  /** 多行控件行数（textarea/variable-input/json）。 */
  rows?: number
  /** M4：enum/const 选项值 → 中文文案（select/radio 节点表单用；缺省显示原始值）。 */
  optionLabels?: Record<string, string>
}

export type WidgetComponent = (props: WidgetProps) => ReactNode
