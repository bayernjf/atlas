/**
 * 节点表单控件注册表（M4，04 §4.10 扩展；自定义控件扩展契约 docs/118 §3.1/§3.2）。
 *
 * 独立于工具表单的全局 defaultRegistry：在「内置九件」之上注册节点业务控件
 * （target-select 等）。节点表单每次挂载 buildNodeRegistry() 得到独立实例，
 * 不污染工具表单；业务控件随节点迁移在此登记，注册列表与 types.ts BUSINESS_WIDGETS
 * 同源（新增业务控件须同时登记两处，杜绝漂移）。
 */
import { buildDefaultRegistry } from './defaultRegistry'
import { CardSelectWidget, CronInputWidget, SavedGraphSelectWidget, TargetSelectWidget, TimezoneInputWidget } from './nodeWidgets'
import type { WidgetRegistry } from './registry'
import { BUSINESS_WIDGETS, CARD_SELECT_WIDGET, CRON_INPUT_WIDGET, TIMEZONE_INPUT_WIDGET, SAVED_GRAPH_SELECT_WIDGET, TARGET_SELECT_WIDGET, type BusinessWidgetName, type WidgetComponent } from './types'

export {
  CARD_SELECT_WIDGET,
  CRON_INPUT_WIDGET,
  SAVED_GRAPH_SELECT_WIDGET,
  TARGET_SELECT_WIDGET,
} from './types'

/** 节点业务控件组件表：键集与 BUSINESS_WIDGETS 逐一对应（同源注册，编译期强制覆盖）。 */
const NODE_WIDGET_COMPONENTS: Record<BusinessWidgetName, WidgetComponent> = {
  [TARGET_SELECT_WIDGET]: TargetSelectWidget,
  [SAVED_GRAPH_SELECT_WIDGET]: SavedGraphSelectWidget,
  [CARD_SELECT_WIDGET]: CardSelectWidget,
  [CRON_INPUT_WIDGET]: CronInputWidget,
  [TIMEZONE_INPUT_WIDGET]: TimezoneInputWidget,
}

/** 节点表单控件表：内置九件 + 全部已登记业务控件（独立实例，注册列表与 BUSINESS_WIDGETS 同源）。 */
export function buildNodeRegistry(): WidgetRegistry {
  const registry = buildDefaultRegistry()
  for (const name of BUSINESS_WIDGETS) registry.register(name, NODE_WIDGET_COMPONENTS[name])
  return registry
}
