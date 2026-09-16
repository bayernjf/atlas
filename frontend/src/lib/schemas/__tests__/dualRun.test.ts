import { describe, expect, it, vi } from 'vitest'
import {
  compareNodeL1WithSchema,
  defaultConfig,
  defaultRetry,
  validateNode,
  type NodeConfig,
  type NodeKind,
} from '../../nodeCatalog'
import { schemaRegistry } from '../index'

function triggerNode(config: NodeConfig) {
  return {
    label: '触发器',
    kind: 'trigger' as NodeKind,
    status: 'idle' as const,
    config,
    retry: defaultRetry(),
  }
}

describe('schemaRegistry', () => {
  it('returns the registered trigger schema at v1', () => {
    expect(schemaRegistry.get('trigger', 'v1').type).toBe('object')
    expect(schemaRegistry.get('trigger')).toBe(schemaRegistry.get('trigger', 'v1'))
  })

  it('rejects unknown kinds and versions', () => {
    expect(() => schemaRegistry.get('not_a_node_kind')).toThrow(/未知节点种类/)
    expect(() => schemaRegistry.get('trigger', 'v2')).toThrow(/无版本/)
  })

  it('registers only migrated kinds during the M1 rollout', () => {
    expect(schemaRegistry.registeredKinds()).toEqual([
      'trigger',
      'ai_decision',
      'tool_call',
      'condition',
      'wait',
    ])
  })
})

describe('trigger dual-run equivalence (U36)', () => {
  const cases: Array<[string, NodeConfig]> = [
    ['manual defaults pass both', defaultConfig('trigger')],
    ['schedule without cron fails both at cron', { ...defaultConfig('trigger'), triggerType: 'schedule', cron: '' }],
    [
      'schedule with whitespace-only cron fails both at cron',
      { ...defaultConfig('trigger'), triggerType: 'schedule', cron: '   ' },
    ],
    [
      'schedule with cron passes both',
      { ...defaultConfig('trigger'), triggerType: 'schedule', cron: '0 9 * * *' },
    ],
    [
      'webhook without url fails both at webhookUrl',
      { ...defaultConfig('trigger'), triggerType: 'webhook', webhookUrl: '' },
    ],
  ]

  it.each(cases)('%s', (_name, config) => {
    expect(compareNodeL1WithSchema('trigger', config)).toBeNull()
  })

  it('logs no divergence error while validating a failing trigger node', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    validateNode(triggerNode({ ...defaultConfig('trigger'), triggerType: 'schedule', cron: '' }))
    expect(errorSpy).not.toHaveBeenCalled()
    errorSpy.mockRestore()
  })
})

describe('ai_decision dual-run equivalence (U36)', () => {
  const cases: Array<[string, NodeConfig]> = [
    ['default config fails promptTemplate on both', defaultConfig('ai_decision')],
    ['whitespace prompt fails promptTemplate on both', { ...defaultConfig('ai_decision'), promptTemplate: ' ' }],
    ['configured node passes both', { ...defaultConfig('ai_decision'), promptTemplate: '通过吗？' }],
    [
      'threshold above range fails confidenceThreshold on both',
      { ...defaultConfig('ai_decision'), promptTemplate: 'x', confidenceThreshold: 1.2 },
    ],
    [
      'threshold below range fails confidenceThreshold on both',
      { ...defaultConfig('ai_decision'), promptTemplate: 'x', confidenceThreshold: -0.1 },
    ],
    ['boundary thresholds pass both', { ...defaultConfig('ai_decision'), promptTemplate: 'x', confidenceThreshold: 0 }],
  ]

  it.each(cases)('%s', (_name, config) => {
    expect(compareNodeL1WithSchema('ai_decision', config)).toBeNull()
  })
})

describe('tool_call dual-run equivalence (U36)', () => {
  const cases: Array<[string, NodeConfig]> = [
    ['default config fails tool on both', defaultConfig('tool_call')],
    ['whitespace tool fails on both', { ...defaultConfig('tool_call'), tool: ' ' }],
    ['selected tool passes both', { ...defaultConfig('tool_call'), tool: 'web/click', params: '{"x":1}' }],
    ['template params do not affect the result', { tool: 'message/send', params: '{{trigger-1.context.payload}}' }],
  ]

  it.each(cases)('%s', (_name, config) => {
    expect(compareNodeL1WithSchema('tool_call', config)).toBeNull()
  })
})

describe('wait dual-run equivalence (U36)', () => {
  const invalidSeconds: Array<[string, number | undefined]> = [
    ['zero', 0],
    ['negative', -1],
    ['above max', 601],
    ['non-integer', 1.5],
    ['undefined', undefined],
  ]

  it.each(invalidSeconds)('%s duration fails durationSeconds on both', (_name, durationSeconds) => {
    expect(
      compareNodeL1WithSchema('wait', { waitType: 'duration', durationSeconds: durationSeconds as number }),
    ).toBeNull()
  })

  it('default config passes both', () => {
    expect(compareNodeL1WithSchema('wait', defaultConfig('wait'))).toBeNull()
  })

  it('non-duration waitType fails waitType on both', () => {
    expect(
      compareNodeL1WithSchema('wait', { waitType: 'event' as 'duration', durationSeconds: 5 }),
    ).toBeNull()
  })
})

describe('condition dual-run equivalence (U36)', () => {
  it('default single empty branch fails the three item fields and defaultTarget on both', () => {
    expect(compareNodeL1WithSchema('condition', defaultConfig('condition'))).toBeNull()
  })

  it('zero branches fails branches on both', () => {
    expect(compareNodeL1WithSchema('condition', { branches: [], defaultTarget: '' })).toBeNull()
  })

  it('configured node passes both', () => {
    expect(
      compareNodeL1WithSchema('condition', {
        branches: [
          { label: '大额', expression: '{{trigger-1.context.payload.amount}} > 1000', target: 'tool-human' },
        ],
        defaultTarget: 'tool-auto',
      }),
    ).toBeNull()
  })

  it('uniqueness, collision and expression syntax stay hand-written-only and do not diverge', () => {
    expect(
      compareNodeL1WithSchema('condition', {
        branches: [
          { label: 'x', expression: 'amount >', target: 'a' },
          { label: 'x', expression: '{{ok}} == null', target: 'a' },
        ],
        defaultTarget: 'a',
      }),
    ).toBeNull()
  })
})
