/**
 * M9 发布流纯函数（发布门禁结论 / 灰度配置构造 / 状态机动作判定）。
 * 不含 React 与 IO，供 ReleaseModal/RolloutModal 与 vitest 共用（U59 ③）。
 * 契约：03 `release_gate`/`rollout_config`、04 §5.11/§5.16、19 §2.3.3。
 */
import type {
  GateConfig,
  GateMetric,
  GateMetricId,
  GateReport,
  ReleaseReportSummary,
  RolloutConfig,
  RolloutRule,
  RolloutStatus,
} from './apiClient'

/** 门控三指标的默认阈值与中文标签（19 §2.3.3 gate.metrics） */
export const GATE_METRIC_SPECS: Array<{
  id: GateMetricId
  label: string
  defaultThreshold: number
  step: number
}> = [
  { id: 'run_error_rate', label: '运行错误率', defaultThreshold: 0.02, step: 0.01 },
  { id: 'manual_escalation_rate', label: '人工升级率', defaultThreshold: 0.1, step: 0.05 },
  { id: 'refund_amount_diff_rate', label: '退款金额差异率', defaultThreshold: 0.005, step: 0.005 },
]

export const ROLLOUT_RULE_ORDER = ['internal', 'lowValueBucket', 'canary', 'full'] as const

/** 发布门禁结论：total=0 未覆盖（不阻塞）、blocked 拦截、否则通过 */
export type GateConclusion = 'blocked' | 'skipped' | 'passed'

export function gateConclusion(report: GateReport): GateConclusion {
  if (report.total === 0 || report.skipped) return 'skipped'
  return report.blocked ? 'blocked' : 'passed'
}

/** 门禁/报告结论中文 meta（U60 ⑧，历史区结论 Tag） */
export const GATE_CONCLUSION_META: Record<GateConclusion, { label: string; color: string }> = {
  blocked: { label: '未通过', color: 'error' },
  skipped: { label: '未覆盖', color: 'warning' },
  passed: { label: '通过', color: 'success' },
}

/** 报告触发方式中文 meta（03 release_report.trigger；手动门禁 vs 发布时门禁） */
export const REPORT_TRIGGER_META: Record<
  ReleaseReportSummary['trigger'],
  { label: string; color: string }
> = {
  manual: { label: '手动门禁', color: 'blue' },
  'publish-gate': { label: '发布门禁', color: 'purple' },
}

/** 历史报告结论（摘要行无 cases，按 total/skipped/blocked 判定，口径同 gateConclusion） */
export function reportConclusion(
  report: Pick<ReleaseReportSummary, 'total' | 'skipped' | 'blocked'>,
): GateConclusion {
  if (report.total === 0 || report.skipped) return 'skipped'
  return report.blocked ? 'blocked' : 'passed'
}

/** ISO 时间 → MM-DD HH:mm（历史趋势表紧凑展示；非法输入原样返回） */
export function reportTimeLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`
}

/** 默认灰度配置：internal 全量 candidate（本租户）+ 低金额桶（≤200 全进）+ canary 5% + 三指标门控 */
export function defaultRolloutConfig(tenant: string): RolloutConfig {
  return {
    strategy: 'progressive',
    rules: [
      { to: 'internal', tenants: [tenant] },
      { to: 'lowValueBucket', field: 'payload.amount', op: '<=', value: 200, percent: 100 },
      { to: 'canary', percent: 5 },
    ],
    gate: defaultGateConfig(),
    inFlightPolicy: 'pin-to-version',
  }
}

export function defaultGateConfig(): GateConfig {
  return {
    observeMinutes: 60,
    autoRollback: true,
    minSamples: 3,
    metrics: GATE_METRIC_SPECS.map((spec) => ({
      id: spec.id,
      threshold: spec.defaultThreshold,
    })),
  }
}

/** 按固定序插入/替换一条规则；rule=null 删除 to 指定段（始终重排为 internal→…→full） */
export function withRule(
  config: RolloutConfig,
  to: RolloutRule['to'],
  rule: RolloutRule | null,
): RolloutConfig {
  const kept = config.rules.filter((item) => item.to !== to)
  const next = rule ? [...kept, rule] : kept
  next.sort(
    (a, b) => ROLLOUT_RULE_ORDER.indexOf(a.to as (typeof ROLLOUT_RULE_ORDER)[number])
      - ROLLOUT_RULE_ORDER.indexOf(b.to as (typeof ROLLOUT_RULE_ORDER)[number]),
  )
  return { ...config, rules: next }
}

export function findRule<T extends RolloutRule['to']>(
  config: RolloutConfig,
  to: T,
): Extract<RolloutRule, { to: T }> | undefined {
  return config.rules.find((rule) => rule.to === to) as Extract<RolloutRule, { to: T }> | undefined
}

/** 新增/更新一条门控指标（同 id 替换） */
export function setGateMetric(config: RolloutConfig, metric: GateMetric): RolloutConfig {
  const metrics = config.gate.metrics.filter((item) => item.id !== metric.id)
  metrics.push(metric)
  const order = GATE_METRIC_SPECS.map((spec) => spec.id)
  metrics.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id))
  return { ...config, gate: { ...config.gate, metrics } }
}

export type RolloutActions = { canStart: boolean; canPromote: boolean; canRollback: boolean }

/** 状态机动作启用（版本数是否足够由调用方结合 versions 判定） */
export function rolloutActions(status: RolloutStatus, versionCount: number): RolloutActions {
  return {
    canStart: status === 'idle' && versionCount >= 2,
    canPromote: status === 'canary',
    canRollback: status === 'canary' || status === 'full',
  }
}

export const ROLLOUT_STATUS_META: Record<RolloutStatus, { label: string; color: string }> = {
  idle: { label: '未开始', color: 'default' },
  canary: { label: '金丝雀中', color: 'processing' },
  full: { label: '全量放量', color: 'success' },
  rolled_back: { label: '已自动/手动回滚', color: 'error' },
}

export function asPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}
