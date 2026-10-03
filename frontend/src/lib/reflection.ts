/**
 * 反思进化 L2 v2 数据层与纯函数（打包 ZS，docs/92）。
 *
 * **只读铁律**（docs/92 E-2 / U1108）：本模块只消费
 * `listReflectionReports`／`getReflectionCandidate`／`listGraphs`／`getGraph`
 * 四只读函数，**不 import、不调用任何写函数**；`READ_ONLY_WHITELIST` 供测试机检
 * （读本文件源码断言 import 面 ⊆ 白名单，U1108 是机检守护不是口头承诺）。
 *
 * 跳转分流（docs/92 E-3）抽成纯函数 `gotoTargetFor`，组件只做渲染与导航。
 */
import {
  getGraph,
  getReflectionCandidate,
  listGraphs,
  listReflectionReports,
  type ReflectionCandidate,
  type ReflectionChange,
  type ReflectionReportItem,
  type ReflectionStatus,
} from './apiClient'

/** 本模块允许从 apiClient 引入的只读面（U1108 机检白名单；加写函数进这里＝自杀式违规）。 */
export const READ_ONLY_WHITELIST = [
  'getGraph',
  'getReflectionCandidate',
  'listGraphs',
  'listReflectionReports',
] as const

export type { ReflectionCandidate, ReflectionChange, ReflectionReportItem, ReflectionStatus }

/** `status` 四值 → AntD Tag 色（docs/92 E-5：仅用颜色区分处必须带文字，页面仍渲染文字标签）。 */
export function statusTone(status: ReflectionStatus): 'success' | 'error' | 'default' {
  switch (status) {
    case 'ok':
      return 'success'
    case 'rejected_whitelist':
    case 'rejected_bounds':
      return 'error'
    case 'no_evidence':
      return 'default'
  }
}

/** 采纳引导目标（docs/92 E-3 分流）；graph_id 缺省（异常数据）→ disabled。 */
export type GotoTarget =
  | { kind: 'editor'; graphId: string }
  | { kind: 'monitoring' }
  | { kind: 'disabled' }

export function gotoTargetFor(
  scope: 'graph_variable' | 'node_config' | 'monitor_rule' | 'gate_config',
  graphId: string | null | undefined,
): GotoTarget {
  if (!graphId) return { kind: 'disabled' }
  switch (scope) {
    case 'graph_variable':
    case 'node_config':
    case 'gate_config':
      return { kind: 'editor', graphId }
    case 'monitor_rule':
      return { kind: 'monitoring' }
  }
}

/** 按 param_key 前缀判 scope（白名单恰四条，docs/92 §0.3）。 */
export function scopeForParamKey(paramKey: string): 'graph_variable' | 'node_config' | 'monitor_rule' | 'gate_config' {
  if (paramKey.startsWith('node.')) return 'node_config'
  if (paramKey.startsWith('monitor.')) return 'monitor_rule'
  if (paramKey.startsWith('gate.')) return 'gate_config'
  return 'graph_variable'
}

/** 报告 → 视图行（U1103：candidate 有无标记；U1106：空列表判定由调用方 isEmptyReports）。 */
export type ReportView = {
  candidateId: string | null
  graphId: string
  baseVersion: number
  status: ReflectionStatus
  hasCandidate: boolean
  reasons: string[]
  generatedAt: string
}

export function toReportView(report: ReflectionReportItem): ReportView {
  return {
    candidateId: report.candidate_id,
    graphId: report.graph_id,
    baseVersion: report.base_version,
    status: report.status,
    hasCandidate: report.candidate_id != null && report.candidate_id !== '',
    reasons: report.reasons,
    generatedAt: report.generated_at,
  }
}

export function filterReportsByGraph(
  reports: ReflectionReportItem[],
  graphId: string | null,
): ReflectionReportItem[] {
  if (!graphId) return reports
  return reports.filter((r) => r.graph_id === graphId)
}

export function isEmptyReports(reports: readonly unknown[]): boolean {
  return reports.length === 0
}

// --- 数据函数（薄封装；页面只经这里取数） -----------------------------------------

export function loadReports(graphId?: string, limit = 50): Promise<ReflectionReportItem[]> {
  return listReflectionReports(graphId, limit)
}

export function loadCandidate(candidateId: string): Promise<ReflectionCandidate> {
  return getReflectionCandidate(candidateId)
}

export function loadGraphOptions(): Promise<{ id: string }[]> {
  return listGraphs()
}

export function loadGraphForEditor(graphId: string): ReturnType<typeof getGraph> {
  return getGraph(graphId)
}
