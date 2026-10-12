import { describe, expect, it } from 'vitest'
import { defaultConfig, NODE_KINDS, type NodeKind } from '../../nodeCatalog'
import { schemaRegistry } from '../index'
import { validateSchemaFields } from '../../validation/l1'
// 打包 AQ（docs/124，ADR T35）起，普通节点的根键表由后端 graph/node_outputs.py 投影构建期
// 生成到 schemas/generated/nodeOutputs.ts（提交入仓、后端守新鲜度），本测试直接对拍生成物，
// 不再经手抄表。trigger/tool_call 不进生成物（前者 context 嵌套、后者动态 outputSchema），
// 作为特例保留本地期望（U35④）。
import { STATIC_OUTPUT_KEYS } from '../generated/nodeOutputs'

const SPECIAL_CASE_OUTPUT_KEYS: Record<string, string[] | Record<string, string[]>> = {
  trigger: { context: ['triggerType', 'cron', 'timezone', 'webhookUrl', 'payload'] },
  tool_call: ['result'],
}

describe('built-in node schemas (U35)', () => {
  it.each([...NODE_KINDS])('%s schema passes MetaSchema self-check via registry', (kind) => {
    expect(() => schemaRegistry.get(kind)).not.toThrow()
  })

  it('registers exactly the built-in kinds（docs/109 打包 AB：+意图识别/信息抽取/内容生成）', () => {
    expect(schemaRegistry.registeredKinds().sort()).toEqual([...NODE_KINDS].sort())
  })

  it.each([...NODE_KINDS])('x-outputSchema of %s matches the generated node-output projection field-by-field', (kind) => {
    const output = schemaRegistry.get(kind)['x-outputSchema']
    const special = SPECIAL_CASE_OUTPUT_KEYS[kind]
    const expected: string[] | Record<string, string[]> = special ?? STATIC_OUTPUT_KEYS[kind]
    expect(expected, `${kind} 既无特例声明也不在生成物根键表中`).toBeTruthy()
    if (Array.isArray(expected)) {
      expect(Object.keys(output.properties ?? {}).sort()).toEqual([...expected].sort())
    } else {
      expect(Object.keys(output.properties ?? {})).toEqual(['context'])
      const contextKeys = Object.keys(output.properties!.context!.properties ?? {})
      expect(contextKeys.sort()).toEqual(expected.context.sort())
    }
  })

  // 打包 AN②：投影表与 x-outputSchema 长期各写一遍、没人对拍，D64 就是这么来的
  // （state_machine 声明四个后端根本不产出的键，而 scope.ts 连条目都没有 ⇒ 引用不校验、
  // 补全给空值）。打包 AQ（docs/124）起投影表是后端单源生成的 nodeOutputs.ts，
  // scope.ts 仅 re-export；本条守护钉的是「生成物 ↔ x-outputSchema」，再加后端新鲜度门
  // （tests/test_node_output_contract.py），三份事实只可能在忘重跑时漂移、且必红。
  //
  // 排除项各有理由，不是"先让门绿"：
  // · trigger 引用走 context 嵌套特例、tool_call 走工具 outputSchema 动态路径（U35④ 既有设计）。
  // 打包 AP（docs/14 D66 闭合）：condition/loop/wait 三条排除项已取消——裁决为「后端 loader
  // 真实产出即权威」，三类声明与 scope.ts 放行根已逐键对齐，纳入对拍后从此不再能漂移。
  const SCOPE_PARITY_EXEMPT: Partial<Record<NodeKind, string>> = {
    trigger: 'context 嵌套特例（U35④）',
    tool_call: '工具 outputSchema 动态路径（U35④）',
  }
  const PARITY_KINDS = [...NODE_KINDS].filter((kind) => !(kind in SCOPE_PARITY_EXEMPT))
  it('每类节点都在对拍覆盖内，排除项必须带理由且仍是两类（防"用排除法把门做绿"）', () => {
    expect([...NODE_KINDS].length - PARITY_KINDS.length).toBe(2)
    expect(Object.values(SCOPE_PARITY_EXEMPT).every(Boolean)).toBe(true)
  })
  it.each(PARITY_KINDS)(
    '生成物可见根键与 %s 的 x-outputSchema 逐字段一致',
    (kind) => {
      const declared = Object.keys(
        (schemaRegistry.get(kind)['x-outputSchema'] as { properties?: object }).properties ?? {},
      ).sort()
      const visible = [...(STATIC_OUTPUT_KEYS[kind] ?? [])].sort()
      expect(visible, `生成物缺 ${kind} 条目＝引用期完全不校验`).toEqual(declared)
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
