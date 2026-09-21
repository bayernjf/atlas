import type { AlertStatus, RunRecord } from './apiClient'

export type RunHealth = 'healthy' | 'unhealthy' | 'error'

export function runHealth(record: RunRecord): RunHealth {
  if (record.status === 'error') return 'error'
  return record.nodes.some((node) => node.status === 'failed') ? 'unhealthy' : 'healthy'
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`
  return `${Math.round(ms)} ms`
}

export function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleTimeString('zh-CN', { hour12: false })
}

// 内置规则名映射到 monitoring namespace 的 i18n key（纯函数不引 hook，由组件 t() 解析）；
// 自定义规则名/rule_id 不经此表，ruleLabel 原样返回、t() 缺键时原样显示（docs/28 §4.2、docs/33 §2）。
export const RULE_LABELS: Record<string, string> = {
  run_error: 'builtinRule.runError',
  node_failed: 'builtinRule.nodeFailed',
  consecutive_failures: 'builtinRule.consecutiveFailures',
  failure_rate: 'builtinRule.failureRateName',
  rollout_gate: 'builtinRule.rolloutGate',
}

/**
 * 告警规则名：自定义规则优先用后端 rule_name；内置规则返回 i18n key（组件 t() 解析）；
 * custom:{cid} 且无 rule_name（PG 档 v1 不持久化）时回退返回 rule_id 原文（docs/28 §4.2）。
 */
export function ruleLabel(ruleId: string, ruleName?: string | null): string {
  if (ruleName) return ruleName
  return RULE_LABELS[ruleId] ?? ruleId
}

export const SEVERITY_COLORS = {
  critical: 'red',
  warning: 'orange',
} as const

// 告警状态映射到 monitoring namespace 的 i18n key（组件 t() 解析，纯函数不引 hook）。
export const ALERT_STATUS_LABELS: Record<AlertStatus, string> = {
  open: 'alertStatus.open',
  acknowledged: 'alertStatus.acknowledged',
  resolved: 'alertStatus.resolved',
}

export const ALERT_STATUS_COLORS: Record<AlertStatus, string> = {
  open: 'red',
  acknowledged: 'gold',
  resolved: 'default',
}
