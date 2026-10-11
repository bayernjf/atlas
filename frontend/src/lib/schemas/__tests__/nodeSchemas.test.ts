import { describe, expect, it } from 'vitest'
import { defaultConfig, NODE_KINDS, type NodeKind } from '../../nodeCatalog'
import { schemaRegistry } from '../index'
import { validateSchemaFields } from '../../validation/l1'
import { STATIC_OUTPUT_KEYS } from '../../scope'

// 与 lib/scope.ts STATIC_OUTPUT_KEYS + trigger/tool_call 特例逐字段对齐（04 §6.5，U35④）。
const EXPECTED_OUTPUT_KEYS: Record<string, string[] | Record<string, string[]>> = {
  trigger: { context: ['triggerType', 'cron', 'timezone', 'webhookUrl', 'payload'] },
  tool_call: ['result'],
  ai_decision: ['decision', 'prompt_rendered', 'memory_injected'],
  condition: ['branch', 'mode', 'target', 'expression_errors', 'expressionErrorCodes', 'expressionErrorParams'],
  loop: ['mode', 'index', 'iterations', 'items', 'item', 'results', 'target', 'exitReason', 'expression_errors', 'expressionErrorCodes', 'expressionErrorParams'],
  parallel: ['status', 'branches', 'joinStrategy', 'joinTarget'],
  wait: ['mode', 'waitType', 'durationMode', 'durationExpression', 'durationSeconds', 'plannedDurationSeconds', 'jitterSeconds', 'absoluteTime', 'eventKey', 'eventKeys', 'eventWaitMode', 'matchedEventKey', 'matchedEventKeys', 'matchedPayloads', 'receivedKeys', 'signaled', 'payload', 'waitedSeconds', 'resolvedBy', 'token'],
  subgraph: ['status', 'outputs'],
  human_approval: ['decision', 'target', 'summary', 'approver', 'resolvedBy', 'comment', 'card'],
  // 打包 AN②（闭合 docs/14 D64）：这行原先钉的是 schema **当时声明**的四个键，与后端产出
  // 不一致（注释里写"D60"是当时的笔误，登记号实为 D64）。现在两边同指后端 loader 的真实
  // 产出，且 lib/scope.ts 也补了 state_machine 条目 ⇒ 这一行从"钉住现状"变成"对齐证明"。
  state_machine: ['mode', 'objectRef', 'from', 'to', 'event', 'state', 'states', 'action'],
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

  // 打包 AN②：这张投影表与上面的 x-outputSchema 长期各写一遍、没人对拍，D64 就是这么来的
  // （state_machine 声明四个后端根本不产出的键，而 scope.ts 连条目都没有 ⇒ 引用不校验、
  // 补全给空值）。本条守护把两边钉成同一组根键。
  //
  // 排除项各有理由，不是"先让门绿"：
  // · trigger 引用走 context 嵌套特例、tool_call 走工具 outputSchema 动态路径（U35④ 既有设计）；
  // · condition/loop/wait 是**本批量出的新漂移**，已登记 docs/14 **D66**：三类的
  //   x-outputSchema 声明的是后端产出面的超集（如 condition 的 expressionErrorCodes、
  //   wait 的 20 个键），而两侧校验表都只放行一小撮根键 ⇒ 补全给得出、编译期 REF_STATIC_PATH_NOT_FOUND
  //   拒，与 D64 同族但要先定权威（docs/03 节点输出契约谁说了算），不在 AN② 范围。
  const SCOPE_PARITY_EXEMPT: Partial<Record<NodeKind, string>> = {
    trigger: 'context 嵌套特例（U35④）',
    tool_call: '工具 outputSchema 动态路径（U35④）',
    condition: 'D66：声明为后端校验表的超集',
    loop: 'D66：同上，且 foreach 有专用分支',
    wait: 'D66：同上（schema 20 键 vs 校验表 3 键）',
  }
  const PARITY_KINDS = [...NODE_KINDS].filter((kind) => !(kind in SCOPE_PARITY_EXEMPT))
  it('每类节点都在对拍覆盖内，排除项必须带理由且仍是五类（防"用排除法把门做绿"）', () => {
    expect([...NODE_KINDS].length - PARITY_KINDS.length).toBe(5)
    expect(Object.values(SCOPE_PARITY_EXEMPT).every(Boolean)).toBe(true)
  })
  it.each(PARITY_KINDS)(
    'scope.ts 的可见根键与 %s 的 x-outputSchema 逐字段一致',
    (kind) => {
      const declared = Object.keys(
        (schemaRegistry.get(kind)['x-outputSchema'] as { properties?: object }).properties ?? {},
      ).sort()
      const visible = [...(STATIC_OUTPUT_KEYS[kind] ?? [])].sort()
      expect(visible, `lib/scope.ts 缺 ${kind} 条目＝引用期完全不校验`).toEqual(declared)
    },
  )

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
