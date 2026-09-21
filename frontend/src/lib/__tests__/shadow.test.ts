import { describe, expect, it } from 'vitest'
import type { ShadowRun, ToolIntent } from '../apiClient'
import {
  autoActionLabel,
  comparisonVerdict,
  formatParameters,
  hasAutoAction,
  intentKind,
  parseShadowInputs,
} from '../shadow'

function intent(partial: Partial<ToolIntent>): ToolIntent {
  return {
    node_id: 'n1',
    tool: 'shop/execute_refund',
    permission: 'financial',
    dry_run: true,
    parameters: null,
    action_status: 'SHADOW_DRY_RUN',
    ...partial,
  }
}

describe('parseShadowInputs', () => {
  it('treats blank text as an empty object', () => {
    expect(parseShadowInputs('')).toEqual({ ok: true, value: {} })
    expect(parseShadowInputs('   \n ')).toEqual({ ok: true, value: {} })
  })

  it('parses a JSON object', () => {
    expect(parseShadowInputs('{"order_id":"12345"}')).toEqual({
      ok: true,
      value: { order_id: '12345' },
    })
  })

  it('rejects invalid JSON with an i18n key', () => {
    expect(parseShadowInputs('{not json')).toEqual({
      ok: false,
      error: 'shadow.error.inputsInvalidJson',
    })
  })

  it('rejects non-object JSON (arrays, primitives)', () => {
    expect(parseShadowInputs('[1,2]')).toEqual({
      ok: false,
      error: 'shadow.error.inputsNotObject',
    })
    expect(parseShadowInputs('"x"')).toEqual({
      ok: false,
      error: 'shadow.error.inputsNotObject',
    })
    expect(parseShadowInputs('null')).toEqual({
      ok: false,
      error: 'shadow.error.inputsNotObject',
    })
  })
})

describe('comparisonVerdict', () => {
  it('maps tri-state match', () => {
    expect(comparisonVerdict(true)).toBe('consistent')
    expect(comparisonVerdict(false)).toBe('mismatch')
    expect(comparisonVerdict(null)).toBe('pending')
  })
})

describe('intentKind', () => {
  it('classifies dry-run, pass-through, simulated and failed intents', () => {
    expect(intentKind(intent({}))).toBe('dryRun')
    expect(intentKind(intent({ dry_run: false, action_status: 'SHADOW_DRY_RUN' }))).toBe('dryRun')
    expect(
      intentKind(intent({ dry_run: false, permission: 'read', action_status: 'SUCCESS' })),
    ).toBe('passThrough')
    expect(intentKind(intent({ dry_run: false, permission: null, action_status: 'SIMULATED' }))).toBe(
      'simulated',
    )
    expect(
      intentKind(intent({ dry_run: false, permission: 'read', action_status: 'FAILED' })),
    ).toBe('failed')
  })
})

describe('autoActionLabel', () => {
  it('maps standard actions to i18n keys, keeps null and unknown actions verbatim', () => {
    expect(autoActionLabel('refunded')).toBe('shadow.action.refunded')
    expect(autoActionLabel('human_review')).toBe('shadow.action.humanReview')
    expect(autoActionLabel(null)).toBeNull()
    expect(autoActionLabel('some_custom_action')).toBe('some_custom_action')
  })
})

describe('formatParameters', () => {
  it('renders non-empty params as pretty JSON and nulls empty', () => {
    expect(formatParameters(null)).toBeNull()
    expect(formatParameters({})).toBeNull()
    expect(formatParameters({ order_id: 'A1' })).toBe('{\n  "order_id": "A1"\n}')
  })
})

describe('hasAutoAction', () => {
  it('is true only when the run inferred an automatic write action', () => {
    const base: ShadowRun = {
      id: 'sr-1',
      graph_id: 'g1',
      inputs: null,
      status: 'completed',
      error: null,
      decisions: [],
      tool_intents: [],
      trace_id: 'tr-1',
      auto_action: null,
      human_outcome: null,
      comparison: { match: null, auto_action: null, human_action: null, diffs: [] },
      created_at: '2026-09-21T00:00:00+00:00',
    }
    expect(hasAutoAction(base)).toBe(false)
    expect(hasAutoAction({ ...base, auto_action: 'refunded' })).toBe(true)
  })
})
