/**
 * 节点表单控件注册表（M4，04 §4.10 扩展）。
 *
 * 独立于工具表单的全局 defaultRegistry：在「内置九件」之上注册节点业务控件
 * （target-select）。节点表单每次挂载 buildNodeRegistry() 得到独立实例，
 * 不污染工具表单；业务控件随节点迁移在此登记。
 */
import { buildDefaultRegistry } from './defaultRegistry'
import { SavedGraphSelectWidget, TargetSelectWidget } from './nodeWidgets'
import type { WidgetRegistry } from './registry'
import { SAVED_GRAPH_SELECT_WIDGET, TARGET_SELECT_WIDGET } from './types'

export { SAVED_GRAPH_SELECT_WIDGET, TARGET_SELECT_WIDGET } from './types'

/** 节点表单控件表：内置九件 + target-select + saved-graph-select（独立实例）。 */
export function buildNodeRegistry(): WidgetRegistry {
  const registry = buildDefaultRegistry()
  registry.register(TARGET_SELECT_WIDGET, TargetSelectWidget)
  registry.register(SAVED_GRAPH_SELECT_WIDGET, SavedGraphSelectWidget)
  return registry
}
