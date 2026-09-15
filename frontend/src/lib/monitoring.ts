import type { AlertStatus, RuleId, RunRecord } from './apiClient'

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

export const RULE_LABELS: Record<RuleId, string> = {
  run_error: '运行异常',
  node_failed: '节点失败',
  consecutive_failures: '连续失败',
  failure_rate: '失败率超标',
}

export function ruleLabel(ruleId: RuleId): string {
  return RULE_LABELS[ruleId] ?? ruleId
}

export const SEVERITY_COLORS = {
  critical: 'red',
  warning: 'orange',
} as const

export const ALERT_STATUS_LABELS: Record<AlertStatus, string> = {
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已关闭',
}

export const ALERT_STATUS_COLORS: Record<AlertStatus, string> = {
  open: 'red',
  acknowledged: 'gold',
  resolved: 'default',
}
