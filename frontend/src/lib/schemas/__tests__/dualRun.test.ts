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
      'loop',
      'parallel',
      'wait',
      'subgraph',
      'human_approval',
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

describe('loop dual-run equivalence (U36)', () => {
  it('default config fails expression and both targets on both', () => {
    expect(compareNodeL1WithSchema('loop', defaultConfig('loop'))).toBeNull()
  })

  it('configured node passes both', () => {
    expect(
      compareNodeL1WithSchema('loop', {
        mode: 'while',
        continueExpression: '{{loop-1.index}} < 3',
        maxIterations: 10,
        bodyTarget: 'tool-body',
        exitTarget: 'tool-exit',
      }),
    ).toBeNull()
  })

  it.each([
    ['zero', 0],
    ['above max', 101],
    ['non-integer', 1.5],
    ['undefined', undefined],
  ])('maxIterations %s fails maxIterations on both', (_name, maxIterations) => {
    expect(
      compareNodeL1WithSchema('loop', {
        mode: 'while',
        continueExpression: '{{loop-1.index}} < 3',
        maxIterations: maxIterations as number,
        bodyTarget: 'b',
        exitTarget: 'e',
      }),
    ).toBeNull()
  })

  it('expression syntax and body/exit collision stay hand-written-only', () => {
    expect(
      compareNodeL1WithSchema('loop', {
        mode: 'while',
        continueExpression: 'index >',
        maxIterations: 0,
        bodyTarget: 'same',
        exitTarget: 'same',
      }),
    ).toBeNull()
  })
})

describe('parallel dual-run equivalence (U36)', () => {
  it('default two empty branches fail both item fields and joinTarget on both', () => {
    expect(compareNodeL1WithSchema('parallel', defaultConfig('parallel'))).toBeNull()
  })

  it('single branch fails branches plus its fields on both', () => {
    expect(
      compareNodeL1WithSchema('parallel', {
        joinStrategy: 'all_success',
        branches: [{ label: 'A', target: '' }],
        joinTarget: '',
      }),
    ).toBeNull()
  })

  it('configured node passes both', () => {
    expect(
      compareNodeL1WithSchema('parallel', {
        joinStrategy: 'all_completed',
        branches: [
          { label: 'A', target: 'tool-a' },
          { label: 'B', target: 'tool-b' },
        ],
        joinTarget: 'tool-join',
      }),
    ).toBeNull()
  })

  it('duplicates and join collision stay hand-written-only', () => {
    expect(
      compareNodeL1WithSchema('parallel', {
        joinStrategy: 'all_success',
        branches: [
          { label: '同', target: 'tool-x' },
          { label: '同', target: 'tool-x' },
        ],
        joinTarget: 'tool-x',
      }),
    ).toBeNull()
  })
})

describe('subgraph dual-run equivalence (U36)', () => {
  it('default config fails graphId on both', () => {
    expect(compareNodeL1WithSchema('subgraph', defaultConfig('subgraph'))).toBeNull()
  })

  it('configured node passes both', () => {
    expect(
      compareNodeL1WithSchema('subgraph', {
        graphId: 'graph-7',
        inputs: { order_id: '{{trigger-1.context.payload.order_id}}' },
      }),
    ).toBeNull()
  })

  it('empty inputs key stays hand-written-only without divergence', () => {
    expect(
      compareNodeL1WithSchema('subgraph', { graphId: 'graph-7', inputs: { '': '{{trigger-1.x}}' } }),
    ).toBeNull()
  })

  it('blank inputs value fails inputs.<key> on both', () => {
    expect(
      compareNodeL1WithSchema('subgraph', { graphId: 'graph-7', inputs: { order_id: '  ' } }),
    ).toBeNull()
  })
})

describe('human_approval dual-run equivalence (U36)', () => {
  it('default config fails summary and both targets on both', () => {
    expect(compareNodeL1WithSchema('human_approval', defaultConfig('human_approval'))).toBeNull()
  })

  it('configured node passes both', () => {
    expect(
      compareNodeL1WithSchema('human_approval', {
        ...defaultConfig('human_approval'),
        summary: '订单 {{trigger-1.context.payload.id}} 退款审批',
        approver: '客服主管',
        approvedTarget: 'tool-approve',
        rejectedTarget: 'tool-reject',
      }),
    ).toBeNull()
  })

  it.each([
    ['below min', 9],
    ['above max', 3601],
    ['non-integer', 1.5],
    ['undefined', undefined],
  ])('timeout %s fails timeoutSeconds on both', (_name, timeoutSeconds) => {
    expect(
      compareNodeL1WithSchema('human_approval', {
        summary: '审批',
        timeoutSeconds: timeoutSeconds as number,
        onTimeout: 'reject',
        approvedTarget: 'a',
        rejectedTarget: 'r',
      }),
    ).toBeNull()
  })

  it('missing targets fail on both and equal targets stay hand-written-only', () => {
    expect(
      compareNodeL1WithSchema('human_approval', {
        ...defaultConfig('human_approval'),
        summary: 'x',
        approvedTarget: '',
        rejectedTarget: '',
      }),
    ).toBeNull()
    expect(
      compareNodeL1WithSchema('human_approval', {
        ...defaultConfig('human_approval'),
        summary: 'x',
        approvedTarget: 'same',
        rejectedTarget: 'same',
      }),
    ).toBeNull()
  })
})
