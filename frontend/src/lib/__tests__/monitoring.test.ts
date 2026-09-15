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
  it('maps rule ids, severity colors and alert status labels to Chinese', () => {
    expect(ruleLabel('run_error')).toBe('运行异常')
    expect(ruleLabel('consecutive_failures')).toBe('连续失败')
    expect(SEVERITY_COLORS.critical).toBe('red')
    expect(ALERT_STATUS_LABELS.open).toBe('待处理')
    expect(formatTime('not-a-date')).toBe('not-a-date')
  })
})
