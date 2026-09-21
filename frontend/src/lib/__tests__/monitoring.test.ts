import { describe, expect, it } from 'vitest'
import type { RunRecord } from '../apiClient'
import {
  ALERT_STATUS_LABELS,
  formatDuration,
  formatTime,
  runHealth,
  ruleLabel,
  SEVERITY_COLORS,
} from '../monitoring'

function record(overrides: Partial<RunRecord> = {}): RunRecord {
  return {
    id: 'run-1',
    graph_id: 'g1',
    mode: 'sync',
    status: 'completed',
    started_at: '2026-09-15T00:00:00+00:00',
    finished_at: '2026-09-15T00:00:01+00:00',
    duration_ms: 12,
    nodes: [{ node_id: 'a', node_type: 'trigger', status: 'success', error: null }],
    error: null,
    ...overrides,
  }
}

describe('runHealth', () => {
  it('classifies completed runs by failed nodes and errors as error', () => {
    expect(runHealth(record())).toBe('healthy')
    expect(
      runHealth(
        record({
          nodes: [
            { node_id: 'a', node_type: 'trigger', status: 'success', error: null },
            { node_id: 'b', node_type: 'tool_call', status: 'failed', error: 'boom' },
          ],
        }),
      ),
    ).toBe('unhealthy')
    expect(runHealth(record({ status: 'error', error: 'RuntimeError: x' }))).toBe('error')
  })
})

describe('formatDuration', () => {
  it('shows null as dash, milliseconds under a second and seconds beyond', () => {
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(12.4)).toBe('12 ms')
    expect(formatDuration(1530)).toBe('1.53 s')
  })
})

describe('monitoring display maps', () => {
  it('maps built-in rule ids and alert status to i18n keys (resolved by t() in the page)', () => {
    expect(ruleLabel('run_error')).toBe('builtinRule.runError')
    expect(ruleLabel('consecutive_failures')).toBe('builtinRule.consecutiveFailures')
    expect(SEVERITY_COLORS.critical).toBe('red')
    expect(ALERT_STATUS_LABELS.open).toBe('alertStatus.open')
    expect(formatTime('not-a-date')).toBe('not-a-date')
  })

  it('docs/28 §4.2 prefers custom rule_name and falls back to rule_id', () => {
    expect(ruleLabel('custom:abc', '错误即告警')).toBe('错误即告警')
    // PG 档 v1 不持久化 rule_name（null）时回退显示 rule_id 原文
    expect(ruleLabel('custom:abc', null)).toBe('custom:abc')
    expect(ruleLabel('custom:abc')).toBe('custom:abc')
  })
})
