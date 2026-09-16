/**
 * 默认控件表与扩展点（M3，04 §4.10 / ADR T17）。
 *
 * 八件内置控件全部由既有 AntD 承载；registerWidget 扩展点导出但 M3 内
 * 零业务调用方（业务控件/控件市场随 docs/14 D29）。
 */
import { WidgetRegistry } from './registry'
import type { WidgetComponent } from './types'
import {
  ExpressionWidget,
  JsonWidget,
  NumberWidget,
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

/** 控件市场扩展点（04 §4.10 / D29）：M3 内零调用方。 */
export function registerWidget(name: string, component: WidgetComponent): void {
  widgetRegistry.register(name, component)
}

/**
 * 取名对应控件组件；未注册控件名一律降级 json 控件
 * （04 §4.10 降级序；FormRenderer 消费，M4 起的节点 schema x-widget 亦走此路）。
 */
export function widgetComponent(
  name: string,
  registry: WidgetRegistry = widgetRegistry,
): WidgetComponent {
  return registry.has(name) ? registry.get(name) : registry.get('json')
}
