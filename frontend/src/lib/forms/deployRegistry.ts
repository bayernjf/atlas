/**
 * 部署配置表单控件注册表（docs/115 §5 第 3 步第一片，2026-10-10）。
 *
 * 独立于工具表单的全局 defaultRegistry 与节点表单 nodeRegistry：在「内置九件」
 * 之上注册部署面业务控件（deploy-gate-metrics）。部署配置每次挂载
 * buildDeployRegistry() 得到独立实例，不污染工具/节点表单；注册列表与
 * types.ts DEPLOY_WIDGETS 同源（新增部署控件须同时登记两处，杜绝漂移）。
 */
import { buildDefaultRegistry } from './defaultRegistry'
import { GateMetricsWidget } from './deployWidgets'
import type { WidgetRegistry } from './registry'
import { DEPLOY_GATE_METRICS_WIDGET, DEPLOY_WIDGETS, type DeployWidgetName, type WidgetComponent } from './types'

/** 部署面业务控件组件表：键集与 DEPLOY_WIDGETS 逐一对应（同源注册，编译期强制覆盖）。 */
const DEPLOY_WIDGET_COMPONENTS: Record<DeployWidgetName, WidgetComponent> = {
  [DEPLOY_GATE_METRICS_WIDGET]: GateMetricsWidget,
}

/** 部署配置表单控件表：内置九件 + 全部已登记部署业务控件（独立实例，注册列表与 DEPLOY_WIDGETS 同源）。 */
export function buildDeployRegistry(): WidgetRegistry {
  const registry = buildDefaultRegistry()
  for (const name of DEPLOY_WIDGETS) registry.register(name, DEPLOY_WIDGET_COMPONENTS[name])
  return registry
}
