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
    expect(() => schemaRegistry.get('ai_decision')).toThrow(/未知节点种类/)
    expect(() => schemaRegistry.get('trigger', 'v2')).toThrow(/无版本/)
  })

  it('registers only migrated kinds during the M1 rollout', () => {
    expect(schemaRegistry.registeredKinds()).toEqual(['trigger'])
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
