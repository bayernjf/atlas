/**
 * 反思进化 L2 数据层与纯函数（打包 ZS docs/92；打包 ZU docs/94 增候选决策与节点定位）。
 *
 * **写调用面白名单**（docs/94 E-7 / U1124）：本模块只消费四只读函数
 * （`listReflectionReports`／`getReflectionCandidate`／`listGraphs`／`getGraph`）
 * 加唯一写函数 `putReflectionDecision`（候选采纳/忽略标记）；`API_WHITELIST` 供测试
 * 机检（读本文件源码断言 import 面 ⊆ 白名单，U1124 是机检守护不是口头承诺）。
 * 标记不改图、不发布、不碰路由（守 T22），真正采纳仍由人经「去修改」跳转后完成。
 *
 * 跳转分流（docs/92 E-3；docs/94 E-6 editor 目标携带 nodeId）抽成纯函数 `gotoTargetFor`，
 * 组件只做渲染与导航。
 */
import {
  getGraph,
  getReflectionCandidate,
  listGraphs,
  listReflectionReports,
  putReflectionDecision,
  type ReflectionCandidate,
  type ReflectionChange,
  type ReflectionDecisionStatus,
  type ReflectionReportItem,
  type ReflectionStatus,
} from './apiClient'

/**
 * 本模块允许从 apiClient 引入的调用面（docs/94 E-7 / U1124 机检白名单）：四只读 +
 * 唯一写 `putReflectionDecision`；加任何其他写函数进这里＝自杀式违规。
 */
export const API_WHITELIST = [
  'getGraph',
  'getReflectionCandidate',
  'listGraphs',
  'listReflectionReports',
  'putReflectionDecision',
] as const

export type {
  ReflectionCandidate,
  ReflectionChange,
  ReflectionDecisionStatus,
  ReflectionReportItem,
  ReflectionStatus,
}

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

/**
 * 候选人工处理标记（docs/94 E-1）：adopted 绿 / dismissed 与 pending（null）灰（default）。
 */
export function decisionTone(
  status: ReflectionDecisionStatus | null | undefined,
): 'success' | 'default' {
  return status === 'adopted' ? 'success' : 'default'
}

/** 决策态 → i18n 键（缺省 pending）。 */
export function decisionLabelKey(
  status: ReflectionDecisionStatus | null | undefined,
): 'decision.adopted' | 'decision.dismissed' | 'decision.pending' {
  if (status === 'adopted') return 'decision.adopted'
  if (status === 'dismissed') return 'decision.dismissed'
  return 'decision.pending'
}

/** 采纳引导目标（docs/92 E-3 分流；docs/94 E-6 editor 目标携带可选 nodeId）；graph_id 缺省 → disabled。 */
export type GotoTarget =
  | { kind: 'editor'; graphId: string; nodeId: string | null }
  | { kind: 'monitoring' }
  | { kind: 'disabled' }

export function gotoTargetFor(
  scope: 'graph_variable' | 'node_config' | 'monitor_rule' | 'gate_config',
  graphId: string | null | undefined,
  nodeId?: string | null,
): GotoTarget {
  if (!graphId) return { kind: 'disabled' }
  if (scope === 'monitor_rule') return { kind: 'monitoring' }
  // graph_variable/node_config/gate_config 都进编辑器；仅 node_config 的
  // node.confidenceThreshold 建议带节点定位，空串/纯空白归一为 null，其余 scope 恒 null。
  const focusedNodeId = scope === 'node_config' && nodeId && nodeId.trim() ? nodeId : null
  return { kind: 'editor', graphId, nodeId: focusedNodeId }
}

/** 按 param_key 前缀判 scope（白名单恰四条，docs/92 §0.3）。 */
export function scopeForParamKey(paramKey: string): 'graph_variable' | 'node_config' | 'monitor_rule' | 'gate_config' {
  if (paramKey.startsWith('node.')) return 'node_config'
  if (paramKey.startsWith('monitor.')) return 'monitor_rule'
  if (paramKey.startsWith('gate.')) return 'gate_config'
  return 'graph_variable'
}

/** 报告 → 视图行（U1103：candidate 有无标记；U1106：空列表判定由调用方 isEmptyReports；ZU：决策态）。 */
export type ReportView = {
  candidateId: string | null
  graphId: string
  baseVersion: number
  status: ReflectionStatus
  hasCandidate: boolean
  reasons: string[]
  generatedAt: string
  decisionStatus: ReflectionDecisionStatus | null
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
    decisionStatus: report.decision_status ?? null,
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

/**
 * 打包 ZU（docs/94 E-1/E-4）：登记/改判候选处理标记。页面唯一写入口，
 * 返回更新后的候选投影供本地刷新；不改图、不发布（守 T22）。
 */
export function markDecision(
  candidateId: string,
  status: ReflectionDecisionStatus,
): Promise<ReflectionCandidate> {
  return putReflectionDecision(candidateId, status)
}

export function loadGraphOptions(): Promise<{ id: string }[]> {
  return listGraphs()
}

export function loadGraphForEditor(graphId: string): ReturnType<typeof getGraph> {
  return getGraph(graphId)
}
