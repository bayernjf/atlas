import { describe, expect, it } from 'vitest'
import {
  extractRefs,
  interpolate,
  isValidVariableName,
  listVariablePaths,
  resolvePath,
  type GraphVariable,
} from '../variables'

const variables: GraphVariable[] = [
  { name: 'company_name', type: 'string', value: 'Atlas', scope: 'global' },
  { name: 'approval_limit', type: 'number', value: '500', scope: 'global' },
]

describe('extractRefs', () => {
  it('extracts paths in 04 §6.3 syntax including whitespace and brackets', () => {
    const template =
      '{{user.name}} / {{ order.items[0].price }} / {{global.company_name}} / {{node_3.result.status}}'
    expect(extractRefs(template)).toEqual([
      'user.name',
      'order.items[0].price',
      'global.company_name',
      'node_3.result.status',
    ])
  })

  it('returns empty list when no refs', () => {
    expect(extractRefs('plain text')).toEqual([])
  })
})

describe('resolvePath', () => {
  const lookup = { order: { items: [{ price: 299 }] }, flag: true }

  it('resolves dotted and bracketed paths', () => {
    expect(resolvePath('order.items[0].price', lookup)).toBe(299)
    expect(resolvePath('flag', lookup)).toBe(true)
  })

  it('returns undefined for missing segments', () => {
    expect(resolvePath('order.items[5].price', lookup)).toBeUndefined()
    expect(resolvePath('nope.x', lookup)).toBeUndefined()
  })
})

describe('interpolate', () => {
  it('replaces known refs and keeps missing ones', () => {
    const result = interpolate('公司 {{global.company_name}}，限额 {{global.missing}}', {
      global: { company_name: 'Atlas' },
    })
    expect(result).toBe('公司 Atlas，限额 {{global.missing}}')
  })
})

describe('isValidVariableName', () => {
  it('accepts identifiers and rejects invalid names', () => {
    expect(isValidVariableName('order_id')).toBe(true)
    expect(isValidVariableName('_secret1')).toBe(true)
    expect(isValidVariableName('1order')).toBe(false)
    expect(isValidVariableName('has space')).toBe(false)
  })
})

describe('listVariablePaths', () => {
  it('lists global vars then per-node outputs by kind', () => {
    const paths = listVariablePaths(variables, [
      { id: 'trigger-1', data: { kind: 'trigger' } },
      { id: 'ai_decision-1', data: { kind: 'ai_decision' } },
      { id: 'tool_call-1', data: { kind: 'tool_call' } },
      { id: 'condition-1', data: { kind: 'condition' } },
      { id: 'loop-1', data: { kind: 'loop' } },
      { id: 'parallel-1', data: { kind: 'parallel' } },
      { id: 'wait-1', data: { kind: 'wait' } },
      { id: 'human-1', data: { kind: 'human_approval' } },
    ])
    expect(paths).toEqual([
      'global.company_name',
      'global.approval_limit',
      'trigger-1.context',
      'ai_decision-1.decision',
      'tool_call-1.result',
      'condition-1.branch',
      'loop-1.index',
      'parallel-1.status',
      'wait-1.durationSeconds',
      'human-1.decision',
    ])
  })
})
