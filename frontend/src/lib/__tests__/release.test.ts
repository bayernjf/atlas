import { describe, expect, it } from 'vitest'
import {
  GATE_CONCLUSION_META,
  GATE_METRIC_SPECS,
  REPORT_TRIGGER_META,
  ROLLOUT_STATUS_META,
  asPercent,
  defaultRolloutConfig,
  findRule,
  gateConclusion,
  reportConclusion,
  reportTimeLabel,
  rolloutActions,
  setGateMetric,
  withRule,
  type GateConclusion,
} from '../release'
import type { GateReport, ReleaseReportSummary, RolloutConfig } from '../apiClient'

function makeReport(overrides: Partial<GateReport>): GateReport {
  return {
    graph_id: 'graph-1',
    target: 'draft',
    total: 0,
    passed: 0,
    failed: 0,
    skipped: false,
    blocked: false,
    cases: [],
    ...overrides,
  }
}

describe('gateConclusion（03 release_gate / 04 §5.11）', () => {
  const cases: Array<[GateReport, GateConclusion]> = [
    [makeReport({ total: 0, skipped: true }), 'skipped'],
    [makeReport({ total: 2, passed: 2, failed: 0, blocked: false, cases: [
      { case_id: 'c1', name: 'a', matches: true, replay_status: 'completed', note: '' },
      { case_id: 'c2', name: 'b', matches: true, replay_status: 'completed', note: '' },
    ] }), 'passed'],
    [makeReport({ total: 2, passed: 1, failed: 1, blocked: true, cases: [
      { case_id: 'c1', name: 'a', matches: true, replay_status: 'completed', note: '' },
      { case_id: 'c2', name: 'b', matches: false, replay_status: 'completed', note: 'tool 输出不一致' },
    ] }), 'blocked'],
  ]
  it.each(cases)('classifies %j', (report, expected) => {
    expect(gateConclusion(report)).toBe(expected)
  })
})

describe('defaultRolloutConfig（19 §2.3.3 金融三硬条款默认）', () => {
  const config = defaultRolloutConfig('t1')
  it('含 internal/低金额桶/canary 三段且固定序', () => {
    expect(config.rules.map((rule) => rule.to)).toEqual(['internal', 'lowValueBucket', 'canary'])
    expect(findRule(config, 'internal')?.tenants).toEqual(['t1'])
    expect(findRule(config, 'lowValueBucket')).toMatchObject({ field: 'payload.amount', op: '<=', percent: 100 })
    expect(findRule(config, 'canary')?.percent).toBe(5)
  })
  it('默认开启自动回滚并纳入三指标', () => {
    expect(config.gate.autoRollback).toBe(true)
    expect(config.gate.metrics.map((metric) => metric.id)).toEqual(GATE_METRIC_SPECS.map((s) => s.id))
    expect(config.inFlightPolicy).toBe('pin-to-version')
  })
})

describe('withRule 固定序插入/替换/删除', () => {
  const base = defaultRolloutConfig('t1')
  it('删除指定段（rule=null）', () => {
    const next = withRule(base, 'canary', null)
    expect(next.rules.map((rule) => rule.to)).toEqual(['internal', 'lowValueBucket'])
  })
  it('插入 full 段后仍按固定序排列', () => {
    const next = withRule(base, 'full', { to: 'full' })
    expect(next.rules.map((rule) => rule.to)).toEqual([
      'internal',
      'lowValueBucket',
      'canary',
      'full',
    ])
  })
  it('替换同段不产生重复', () => {
    const next = withRule(base, 'canary', { to: 'canary', percent: 25 })
    expect(next.rules.filter((rule) => rule.to === 'canary')).toHaveLength(1)
    expect(findRule(next, 'canary')?.percent).toBe(25)
  })
})

describe('setGateMetric', () => {
  it('新增指标按规格顺序排列', () => {
    const config: RolloutConfig = {
      ...defaultRolloutConfig('t1'),
      gate: { observeMinutes: 30, autoRollback: true, minSamples: 3, metrics: [] },
    }
    const next = setGateMetric(config, { id: 'manual_escalation_rate', threshold: 0.2 })
    expect(next.gate.metrics.map((metric) => metric.id)).toEqual(['manual_escalation_rate'])
    const withError = setGateMetric(next, { id: 'run_error_rate', threshold: 0.01 })
    expect(withError.gate.metrics.map((metric) => metric.id)).toEqual([
      'run_error_rate',
      'manual_escalation_rate',
    ])
  })
  it('同 id 替换阈值', () => {
    const config = defaultRolloutConfig('t1')
    const next = setGateMetric(config, { id: 'run_error_rate', threshold: 0.05 })
    expect(next.gate.metrics.find((metric) => metric.id === 'run_error_rate')?.threshold).toBe(0.05)
    expect(next.gate.metrics).toHaveLength(3)
  })
})

describe('rolloutActions 状态机（03 rollout_config）', () => {
  it('idle 且版本不足不能 start', () => {
    expect(rolloutActions('idle', 1)).toEqual({ canStart: false, canPromote: false, canRollback: false })
  })
  it('idle 且 ≥2 版本可 start', () => {
    expect(rolloutActions('idle', 2).canStart).toBe(true)
  })
  it('canary 可 promote/rollback', () => {
    expect(rolloutActions('canary', 2)).toEqual({ canStart: false, canPromote: true, canRollback: true })
  })
  it('full 仅可 rollback', () => {
    expect(rolloutActions('full', 2)).toEqual({ canStart: false, canPromote: false, canRollback: true })
  })
  it('rolled_back 全部禁用', () => {
    expect(rolloutActions('rolled_back', 2)).toEqual({
      canStart: false,
      canPromote: false,
      canRollback: false,
    })
  })
})

describe('状态元数据与百分比', () => {
  it('四个状态都有中文标签与颜色', () => {
    expect(Object.keys(ROLLOUT_STATUS_META).sort()).toEqual(
      ['canary', 'full', 'idle', 'rolled_back'].sort(),
    )
  })
  it('asPercent 处理 null/undefined 与数值', () => {
    expect(asPercent(null)).toBe('—')
    expect(asPercent(undefined)).toBe('—')
    expect(asPercent(0.125)).toBe('12.5%')
  })
})

function makeSummary(overrides: Partial<ReleaseReportSummary>): ReleaseReportSummary {
  return {
    id: 'rr-1',
    graph_id: 'graph-1',
    target: 'draft',
    trigger: 'manual',
    total: 0,
    passed: 0,
    failed: 0,
    skipped: true,
    blocked: false,
    pass_rate: null,
    created_at: '2026-09-18T10:00:00+00:00',
    ...overrides,
  }
}

describe('D26 报告 v1：reportConclusion（U60 ⑧，口径同 gateConclusion）', () => {
  it('total=0/skipped → 未覆盖', () => {
    expect(reportConclusion(makeSummary({}))).toBe('skipped')
  })
  it('全部一致 → 通过', () => {
    expect(
      reportConclusion(
        makeSummary({ total: 3, passed: 3, failed: 0, skipped: false, pass_rate: 1 }),
      ),
    ).toBe('passed')
  })
  it('任一不匹配 → 未通过', () => {
    expect(
      reportConclusion(
        makeSummary({ total: 3, passed: 2, failed: 1, skipped: false, blocked: true, pass_rate: 2 / 3 }),
      ),
    ).toBe('blocked')
  })
  it('三态结论 meta 与两种 trigger meta 均持有 i18n 标签键与颜色（docs/57）', () => {
    for (const key of ['blocked', 'skipped', 'passed'] as const) {
      expect(GATE_CONCLUSION_META[key].label).toMatch(/^release\.conclusion\./)
      expect(GATE_CONCLUSION_META[key].color).toBeTruthy()
    }
    expect(REPORT_TRIGGER_META.manual.label).toBe('release.trigger.manual')
    expect(REPORT_TRIGGER_META['publish-gate'].label).toBe('release.trigger.publishGate')
  })
})

describe('D26 报告 v1：reportTimeLabel', () => {
  it('ISO 时间格式化为 MM-DD HH:mm', () => {
    expect(reportTimeLabel('2026-09-18T10:05:00+00:00')).toMatch(/^09-\d{2} \d{2}:\d{2}$/)
  })
  it('非法时间原样返回（不抛错）', () => {
    expect(reportTimeLabel('not-a-date')).toBe('not-a-date')
  })
})
