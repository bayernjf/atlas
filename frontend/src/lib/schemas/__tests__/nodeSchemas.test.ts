import { describe, expect, it } from 'vitest'
import { defaultConfig, NODE_KINDS, type NodeKind } from '../../nodeCatalog'
import { schemaRegistry } from '../index'
import { validateSchemaFields } from '../../validation/l1'

// 与 lib/scope.ts STATIC_OUTPUT_KEYS + trigger/tool_call 特例逐字段对齐（04 §6.5，U35④）。
const EXPECTED_OUTPUT_KEYS: Record<string, string[] | Record<string, string[]>> = {
  trigger: { context: ['triggerType', 'cron', 'webhookUrl', 'payload'] },
  tool_call: ['result'],
  ai_decision: ['decision', 'prompt_rendered'],
  condition: ['branch', 'mode', 'target'],
  loop: ['mode', 'index', 'iterations', 'items', 'item', 'results', 'target', 'exitReason', 'expression_errors'],
  parallel: ['status', 'branches', 'joinStrategy', 'joinTarget'],
  wait: ['mode', 'waitType', 'durationMode', 'durationExpression', 'durationSeconds', 'plannedDurationSeconds', 'jitterSeconds', 'absoluteTime', 'eventKey', 'eventKeys', 'matchedEventKey', 'signaled', 'payload', 'waitedSeconds', 'resolvedBy', 'token'],
  subgraph: ['status', 'outputs'],
  human_approval: ['decision', 'target', 'summary', 'approver', 'resolvedBy', 'comment', 'card'],
}

describe('nine built-in node schemas (U35)', () => {
  it.each([...NODE_KINDS])('%s schema passes MetaSchema self-check via registry', (kind) => {
    expect(() => schemaRegistry.get(kind)).not.toThrow()
  })

  it('registers exactly the nine built-in kinds', () => {
    expect(schemaRegistry.registeredKinds().sort()).toEqual([...NODE_KINDS].sort())
  })

  it.each([...NODE_KINDS])('x-outputSchema of %s matches the scope.ts projection field-by-field', (kind) => {
    const output = schemaRegistry.get(kind)['x-outputSchema']
    const expected = EXPECTED_OUTPUT_KEYS[kind]
    if (Array.isArray(expected)) {
      expect(Object.keys(output.properties ?? {}).sort()).toEqual([...expected].sort())
    } else {
      expect(Object.keys(output.properties ?? {})).toEqual(['context'])
      const contextKeys = Object.keys(output.properties!.context!.properties ?? {})
      expect(contextKeys.sort()).toEqual(expected.context.sort())
    }
  })

  it('parallel x-outputSchema omits the dynamic result key and subgraph exposes only roots', () => {
    const parallel = schemaRegistry.get('parallel')['x-outputSchema'].properties ?? {}
    expect('result' in parallel).toBe(false)
    const subgraph = schemaRegistry.get('subgraph')['x-outputSchema'].properties ?? {}
    expect(Object.keys(subgraph.outputs ?? {})).toEqual([])
  })
})

describe('schemaRegistry', () => {
  it('returns the registered trigger schema at v1', () => {
    expect(schemaRegistry.get('trigger', 'v1').type).toBe('object')
    expect(schemaRegistry.get('trigger')).toBe(schemaRegistry.get('trigger', 'v1'))
  })

  it('rejects unknown kinds and versions', () => {
    expect(() => schemaRegistry.get('not_a_node_kind')).toThrow(/未知节点种类/)
    expect(() => schemaRegistry.get('trigger', 'v2')).toThrow(/无版本/)
  })
})

describe('default configs are structurally schema-valid apart from blank/missing fields (U36②)', () => {
  it.each([...NODE_KINDS])('%s default config only trips required/pattern diagnostics', (kind) => {
    const findings = validateSchemaFields(schemaRegistry.get(kind), defaultConfig(kind as NodeKind))
    for (const finding of findings) {
      expect(['FIELD_REQUIRED', 'FIELD_PATTERN']).toContain(finding.code)
    }
  })

  it('trigger and wait defaults validate clean', () => {
    expect(validateSchemaFields(schemaRegistry.get('trigger'), defaultConfig('trigger'))).toEqual([])
    expect(validateSchemaFields(schemaRegistry.get('wait'), defaultConfig('wait'))).toEqual([])
  })
})
