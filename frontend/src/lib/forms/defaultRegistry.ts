/**
 * 默认控件表与扩展点（M3 八件 / 04 §4.10 / ADR T17；M4 节点迁移补 radio 共九件）。
 *
 * 内置控件全部由既有 AntD 承载；registerWidget 扩展点导出，节点业务控件
 * （target-select 等）在 nodeWidgets.tsx 经 buildNodeRegistry 注册，不污染本表。
 */
import { WidgetRegistry } from './registry'
import { BUILTIN_WIDGETS, type WidgetComponent } from './types'
import {
  ExpressionWidget,
  JsonWidget,
  NumberWidget,
  RadioWidget,
  SelectWidget,
  SwitchWidget,
  TextareaWidget,
  TextWidget,
  VariableInputWidget,
} from './widgets'

const BUILTINS: Record<string, WidgetComponent> = {
  text: TextWidget,
  number: NumberWidget,
  select: SelectWidget,
  radio: RadioWidget,
  textarea: TextareaWidget,
  switch: SwitchWidget,
  json: JsonWidget,
  expression: ExpressionWidget,
  'variable-input': VariableInputWidget,
}

export function buildDefaultRegistry(): WidgetRegistry {
  const registry = new WidgetRegistry()
  for (const [name, component] of Object.entries(BUILTINS)) registry.register(name, component)
  return registry
}

/** 默认注册表：八件内置控件。 */
export const widgetRegistry = buildDefaultRegistry()

/** 控件市场扩展点（04 §4.10 / D29；自定义控件扩展契约 docs/118 §3.1，2026-10-10）。
 *
 * 静态校验（开发期 fail-fast）：名称非空、不得与内置九件冲突、组件必须是函数组件；
 * 违反即 throw，防止 schema 写错控件名或误覆盖内置控件后静默出错。
 */
export function registerWidget(name: string, component: WidgetComponent): void {
  if (typeof name !== 'string' || name.length === 0) {
    throw new Error(`WidgetRegistry：控件名必须是非空字符串，收到 ${JSON.stringify(name)}`)
  }
  if (BUILTIN_WIDGETS.includes(name as (typeof BUILTIN_WIDGETS)[number])) {
    throw new Error(`WidgetRegistry：控件名 "${name}" 与内置控件冲突，禁止覆盖内置控件`)
  }
  if (typeof component !== 'function') {
    throw new Error(`WidgetRegistry：控件 "${name}" 的组件必须是函数组件，收到 ${typeof component}`)
  }
  widgetRegistry.register(name, component)
}

/**
 * 取名对应控件组件；未注册控件名一律降级 json 控件
 * （04 §4.10 降级序；自定义控件扩展契约 docs/118 §3.4：运行期降级 json＋console.warn，
 * 开发期 WidgetRegistry.get() 对未注册名的 throw 语义保留）。
 * FormRenderer 消费，M4 起的节点 schema x-widget 亦走此路。
 */
export function widgetComponent(
  name: string,
  registry: WidgetRegistry = widgetRegistry,
): WidgetComponent {
  if (registry.has(name)) return registry.get(name)
  if (name !== 'json') {
    console.warn(`WidgetRegistry：未注册控件 "${name}"，已降级 json 控件`)
  }
  return registry.get('json')
}
