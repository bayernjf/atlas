import { describe, expect, it } from 'vitest'
import { defaultConfig, NODE_KINDS, type NodeKind } from '../../nodeCatalog'
import { schemaRegistry } from '../index'
import { validateConfigBySchema } from '../validateConfig'

// 与 lib/scope.ts STATIC_OUTPUT_KEYS + trigger/tool_call 特例逐字段对齐（04 §6.5，U35④）。
const EXPECTED_OUTPUT_KEYS: Record<string, string[] | Record<string, string[]>> = {
  trigger: { context: ['triggerType', 'cron', 'webhookUrl', 'payload'] },
  tool_call: ['result'],
  ai_decision: ['decision', 'prompt_rendered'],
  condition: ['branch', 'target'],
  loop: ['index', 'iterations'],
  parallel: ['status', 'branches', 'joinStrategy', 'joinTarget'],
  wait: ['mode', 'waitType', 'durationSeconds'],
  subgraph: ['status', 'outputs'],
  human_approval: ['decision', 'target', 'summary', 'approver', 'resolvedBy'],
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

describe('default configs are structurally schema-valid apart from blank/missing fields (U36②)', () => {
  it.each([...NODE_KINDS])('%s default config only trips required/pattern diagnostics', (kind) => {
    const diagnostics = validateConfigBySchema(schemaRegistry.get(kind), defaultConfig(kind as NodeKind))
    for (const diagnostic of diagnostics) {
      expect(['required', 'pattern']).toContain(diagnostic.rule)
    }
  })

  it('trigger and wait defaults validate clean', () => {
    expect(validateConfigBySchema(schemaRegistry.get('trigger'), defaultConfig('trigger'))).toEqual([])
    expect(validateConfigBySchema(schemaRegistry.get('wait'), defaultConfig('wait'))).toEqual([])
  })
})
