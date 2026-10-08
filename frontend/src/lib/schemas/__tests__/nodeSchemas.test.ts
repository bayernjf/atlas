import { describe, expect, it } from 'vitest'
import { defaultConfig, NODE_KINDS, type NodeKind } from '../../nodeCatalog'
import { schemaRegistry } from '../index'
import { validateSchemaFields } from '../../validation/l1'

// 与 lib/scope.ts STATIC_OUTPUT_KEYS + trigger/tool_call 特例逐字段对齐（04 §6.5，U35④）。
const EXPECTED_OUTPUT_KEYS: Record<string, string[] | Record<string, string[]>> = {
  trigger: { context: ['triggerType', 'cron', 'timezone', 'webhookUrl', 'payload'] },
  tool_call: ['result'],
  ai_decision: ['decision', 'prompt_rendered'],
  condition: ['branch', 'mode', 'target', 'expression_errors', 'expressionErrorCodes', 'expressionErrorParams'],
  loop: ['mode', 'index', 'iterations', 'items', 'item', 'results', 'target', 'exitReason', 'expression_errors', 'expressionErrorCodes', 'expressionErrorParams'],
  parallel: ['status', 'branches', 'joinStrategy', 'joinTarget'],
  wait: ['mode', 'waitType', 'durationMode', 'durationExpression', 'durationSeconds', 'plannedDurationSeconds', 'jitterSeconds', 'absoluteTime', 'eventKey', 'eventKeys', 'eventWaitMode', 'matchedEventKey', 'matchedEventKeys', 'matchedPayloads', 'receivedKeys', 'signaled', 'payload', 'waitedSeconds', 'resolvedBy', 'token'],
  subgraph: ['status', 'outputs'],
  human_approval: ['decision', 'target', 'summary', 'approver', 'resolvedBy', 'comment', 'card'],
  intent_recognition: ['result', 'prompt_rendered'],
  info_extraction: ['result', 'prompt_rendered'],
  content_generation: ['result', 'prompt_rendered'],
}

describe('built-in node schemas (U35)', () => {
  it.each([...NODE_KINDS])('%s schema passes MetaSchema self-check via registry', (kind) => {
    expect(() => schemaRegistry.get(kind)).not.toThrow()
  })

  it('registers exactly the built-in kinds（docs/109 打包 AB：+意图识别/信息抽取/内容生成）', () => {
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

describe('structured nodes schema→form contract (docs/109 打包 AB, U1276)', () => {
  it('accepts an appended intent (array add) and requires name', () => {
    const schema = schemaRegistry.get('intent_recognition')
    const config = defaultConfig('intent_recognition')
    config.intents = [
      { name: '查余额', description: '查询账户余额' },
      { name: '转账', description: '向他人转账' },
    ]
    expect(validateSchemaFields(schema, config)).toEqual([])
    expect(validateSchemaFields(schema, { ...config, intents: [{ name: '  ' }] }).map((f) => f.code)).toEqual(
      expect.arrayContaining(['FIELD_PATTERN']),
    )
  })

  it('enforces the field type enum (type selection) for info_extraction', () => {
    const schema = schemaRegistry.get('info_extraction')
    const config = defaultConfig('info_extraction')
    config.fields = [{ name: 'amount', type: 'number' }, { name: 'note', type: 'string' }]
    expect(validateSchemaFields(schema, config)).toEqual([])
    const bad = validateSchemaFields(schema, { ...config, fields: [{ name: 'x', type: 'date' }] })
    expect(bad.map((f) => f.code)).toEqual(expect.arrayContaining(['FIELD_ENUM']))
  })

  it('content_generation requires a non-empty template and bounds maxLength', () => {
    const schema = schemaRegistry.get('content_generation')
    const config = defaultConfig('content_generation')
    config.template = '处理订单 {{trigger-1.context.payload.order_id}}'
    expect(validateSchemaFields(schema, config)).toEqual([])
    const missing = validateSchemaFields(schema, { ...defaultConfig('content_generation'), maxLength: 99999 })
    expect(missing.map((f) => f.code)).toEqual(expect.arrayContaining(['FIELD_RANGE', 'FIELD_PATTERN']))
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
