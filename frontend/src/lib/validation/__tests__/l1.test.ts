import { describe, expect, it } from 'vitest'
import { defaultConfig, type NodeConfig, type NodeKind } from '../../nodeCatalog'
import type { MetaSchema } from '../../schemas/metaSchema'
import { schemaRegistry } from '../../schemas'
import {
  eventKeyStaticValid,
  FIELD_CODES,
  validEventKey,
  validateNodeFields,
  validateParamFields,
  validateSchemaFields,
} from '../l1'

function fields(kind: NodeKind, config?: NodeConfig) {
  return validateNodeFields(kind, config ?? defaultConfig(kind))
}

function pointers(kind: NodeKind, config?: NodeConfig): string[] {
  return fields(kind, config).map((diagnostic) => diagnostic.loc.pointer ?? '')
}

describe('validateNodeFields shape (U37①②)', () => {
  it('emits layer:field error diagnostics with RFC6901 pointers and Chinese messages', () => {
    const diagnostics = fields('condition')
    expect(diagnostics.length).toBeGreaterThan(0)
    for (const diagnostic of diagnostics) {
      expect(diagnostic.severity).toBe('error')
      expect(diagnostic.layer).toBe('field')
      expect(diagnostic.code).toMatch(/^FIELD_/)
      expect(diagnostic.message).toMatch(/[一-鿿]/)
      expect(diagnostic.loc.nodeId).toBeUndefined()
      expect(diagnostic.quickFix).toBeUndefined()
    }
  })

  it('converts dotted bracket paths to RFC6901 pointers including branches', () => {
    const pointerSet = new Set(pointers('condition'))
    expect(pointerSet).toContain('/branches/0/label')
    expect(pointerSet).toContain('/branches/0/expression')
    expect(pointerSet).toContain('/branches/0/target')
    expect(pointerSet).toContain('/defaultTarget')
    expect(pointers('ai_decision')).toContain('/promptTemplate')
  })

  it('escapes pointer tokens per RFC6901', () => {
    const diagnostics = fields('subgraph', {
      ...defaultConfig('subgraph'),
      graphId: 'g-1',
      inputs: { 'a/b': '  ' },
    })
    expect(diagnostics.some((d) => d.loc.pointer === '/inputs/a~1b')).toBe(true)
  })
})

describe('schema keyword to FIELD_* code mapping (U37②)', () => {
  function codesOf(schema: MetaSchema, config: unknown): string[] {
    return validateSchemaFields(schema, config).map((finding) => finding.code)
  }

  it('covers the full FIELD_* code set on synthetic schemas', () => {
    expect(codesOf({ type: 'object', required: ['a'] }, {})).toContain(FIELD_CODES.REQUIRED)
    expect(codesOf({ type: 'object', properties: { a: { type: 'string' } } }, { a: 1 })).toContain(
      FIELD_CODES.TYPE,
    )
    expect(codesOf({ type: 'object', properties: { a: { enum: ['x', 'y'] } } }, { a: 'z' })).toContain(
      FIELD_CODES.ENUM,
    )
    expect(codesOf({ type: 'object', properties: { a: { const: 'x' } } }, { a: 'y' })).toContain(
      FIELD_CODES.CONST,
    )
    expect(codesOf({ type: 'object', properties: { a: { type: 'number', minimum: 0, maximum: 1 } } }, { a: 2 })).toContain(
      FIELD_CODES.RANGE,
    )
    expect(
      codesOf(
        { type: 'object', properties: { a: { type: 'string', minLength: 2, maxLength: 3 } } },
        { a: 'x' },
      ),
    ).toContain(FIELD_CODES.LENGTH)
    expect(codesOf({ type: 'object', properties: { a: { type: 'string', pattern: '^\\d+$' } } }, { a: 'x' })).toContain(
      FIELD_CODES.PATTERN,
    )
    expect(codesOf({ type: 'object', properties: { a: { type: 'array', minItems: 2 } } }, { a: [] })).toContain(
      FIELD_CODES.ITEMS_MIN,
    )
    expect(codesOf({ type: 'object', properties: { a: { type: 'array', maxItems: 1 } } }, { a: [1, 2] })).toContain(
      FIELD_CODES.ITEMS_MAX,
    )
    expect(codesOf({ type: 'object', properties: {}, additionalProperties: false }, { a: 1 })).toContain(
      FIELD_CODES.ADDITIONAL_PROPERTIES,
    )
  })

  it('maps node keywords to concrete FIELD_* codes', () => {
    const waitCodes = new Set(
      validateSchemaFields(schemaRegistry.get('wait'), { waitType: 'duration', durationSeconds: 0 }).map(
        (f) => f.code,
      ),
    )
    expect(waitCodes.has(FIELD_CODES.RANGE)).toBe(true)

    const triggerCodes = new Set(
      validateSchemaFields(schemaRegistry.get('trigger'), { triggerType: 'schedule', cron: '' }).map(
        (f) => f.code,
      ),
    )
    expect(triggerCodes.has(FIELD_CODES.PATTERN)).toBe(true)

    const single = codesOf(
      schemaRegistry.get('parallel'),
      { joinStrategy: 'all_success', branches: [{ label: 'A', target: 'a' }], joinTarget: 'j' },
    )
    expect(single).toContain(FIELD_CODES.ITEMS_MIN)

    expect(
      codesOf(schemaRegistry.get('wait'), { waitType: 'until', durationSeconds: 5 }),
    ).toContain(FIELD_CODES.CONST)
  })

  it('keeps the M1 oneOf candidate-branch semantics on trigger', () => {
    // manual：cron/webhookUrl 均空也零字段错误（候选分支零叶子错误）。
    expect(validateSchemaFields(schemaRegistry.get('trigger'), defaultConfig('trigger'))).toEqual([])
    // schedule 缺 cron 键 → required；不回灌其他分支的 const 判别错误。
    const missingCron = validateSchemaFields(schemaRegistry.get('trigger'), { triggerType: 'schedule' })
    expect(missingCron.map((f) => f.pointer)).toEqual(['/cron'])
    expect(missingCron[0].code).toBe(FIELD_CODES.REQUIRED)
  })
})

describe('event wait v1 (docs/47)', () => {
  it('validEventKey accepts rendered keys with [A-Za-z0-9:_-] and rejects others', () => {
    expect(validEventKey('order_paid')).toBe(true)
    expect(validEventKey('evt:paid-x_1')).toBe(true)
    for (const bad of ['', 'has space', 'a/b', 'a.b', '中文', 'a'.repeat(129)]) {
      expect(validEventKey(bad)).toBe(false)
    }
  })

  it('eventKeyStaticValid ignores placeholder contents but checks static parts', () => {
    expect(eventKeyStaticValid('order_paid_{{trigger-1.context.payload.order_id}}')).toBe(true)
    expect(eventKeyStaticValid('{{x}}')).toBe(true)
    expect(eventKeyStaticValid('bad key {{x}}')).toBe(false)
    expect(eventKeyStaticValid('k/{{x}}')).toBe(false)
  })

  it('accepts a complete event wait config', () => {
    expect(
      fields('wait', {
        waitType: 'event',
        eventKey: 'order_paid_{{trigger-1.context.payload.id}}',
        timeoutSeconds: 300,
        onTimeout: 'continue',
      }),
    ).toEqual([])
  })

  it('flags event branch field errors on the right pointers', () => {
    const diagnostics = fields('wait', {
      waitType: 'event',
      eventKey: 'bad key',
      timeoutSeconds: 86401,
      onTimeout: 'abort' as 'continue',
    })
    const byPointer = new Map(diagnostics.map((d) => [d.loc.pointer, d]))
    expect(byPointer.get('/eventKey')?.code).toBe(FIELD_CODES.PATTERN)
    expect(byPointer.get('/timeoutSeconds')?.message).toContain('1-86400')
    expect(byPointer.get('/onTimeout')?.message).toContain('继续或失败')
  })
})

describe('dynamic wait duration v1 (docs/49)', () => {
  it('accepts a complete dynamic wait config', () => {
    expect(
      fields('wait', {
        waitType: 'duration',
        durationMode: 'dynamic',
        durationExpression: '{{global.slaHours}} * 3600',
      }),
    ).toEqual([])
  })

  it('accepts a dynamic config retaining a stale durationSeconds', () => {
    expect(
      fields('wait', {
        waitType: 'duration',
        durationMode: 'dynamic',
        durationExpression: '{{global.waitSecs}}',
        durationSeconds: 5,
      }),
    ).toEqual([])
  })

  it('flags missing or overlong expression on /durationExpression', () => {
    const missing = fields('wait', {
      waitType: 'duration',
      durationMode: 'dynamic',
    })
    expect(missing.map((d) => d.loc.pointer)).toEqual(['/durationExpression'])
    expect(missing[0].code).toBe(FIELD_CODES.REQUIRED)

    const overlong = fields('wait', {
      waitType: 'duration',
      durationMode: 'dynamic',
      durationExpression: 'x'.repeat(201),
    })
    expect(overlong.map((d) => d.loc.pointer)).toEqual([
      '/durationExpression',
      '/durationExpression',
    ])
    expect(overlong.every((d) => d.code === FIELD_CODES.LENGTH)).toBe(true)
  })

  it('flags missing durationSeconds only in static mode', () => {
    const diagnostics = fields('wait', { waitType: 'duration', durationMode: 'static' })
    expect(diagnostics.map((d) => d.loc.pointer)).toEqual(['/durationSeconds'])
  })

  it('accepts a complete static config', () => {
    expect(
      fields('wait', {
        waitType: 'duration',
        durationMode: 'static',
        durationSeconds: 10,
      }),
    ).toEqual([])
  })
})

describe('absolute wait time v1 (docs/50)', () => {
  it('accepts a complete absolute wait config with stale duration fields', () => {
    expect(
      fields('wait', {
        waitType: 'duration',
        durationMode: 'absolute',
        absoluteTime: '2026-09-23T18:00:00+08:00',
        durationSeconds: 5,
        durationExpression: '{{global.x}}',
      }),
    ).toEqual([])
  })

  it('accepts whitespace-padded values', () => {
    expect(
      fields('wait', {
        waitType: 'duration',
        durationMode: 'absolute',
        absoluteTime: ` ${'2026-09-23T10:00:00+00:00'.padEnd(60, '0')} `,
      }),
    ).toEqual([])
  })

  it('flags missing or overlong absoluteTime on /absoluteTime', () => {
    const missing = fields('wait', {
      waitType: 'duration',
      durationMode: 'absolute',
    })
    expect(missing.map((d) => d.loc.pointer)).toEqual(['/absoluteTime'])
    expect(missing[0].code).toBe(FIELD_CODES.REQUIRED)

    const blank = fields('wait', {
      waitType: 'duration',
      durationMode: 'absolute',
      absoluteTime: '   ',
    })
    expect(blank.map((d) => d.loc.pointer)).toEqual(['/absoluteTime'])

    const overlong = fields('wait', {
      waitType: 'duration',
      durationMode: 'absolute',
      absoluteTime: 'x'.repeat(65),
    })
    expect(overlong.map((d) => d.loc.pointer)).toEqual([
      '/absoluteTime',
      '/absoluteTime',
    ])
    expect(overlong.every((d) => d.code === FIELD_CODES.LENGTH)).toBe(true)
  })
})

describe('message parity with M1 hand-written copy (U37②)', () => {
  function messages(kind: NodeKind, config: NodeConfig): string[] {
    return fields(kind, config).map((d) => d.message)
  }

  it('trigger / ai_decision / tool_call', () => {
    expect(messages('trigger', { ...defaultConfig('trigger'), triggerType: 'schedule', cron: '' })).toContain(
      '定时触发必须填写 Cron 表达式',
    )
    expect(messages('trigger', { ...defaultConfig('trigger'), triggerType: 'webhook', webhookUrl: '' })).toContain(
      'Webhook 触发必须填写 URL',
    )
    expect(messages('ai_decision', defaultConfig('ai_decision'))).toContain('AI 决策必须填写提示词模板')
    expect(
      messages('ai_decision', { ...defaultConfig('ai_decision'), promptTemplate: 'x', confidenceThreshold: 1.2 }),
    ).toContain('置信度阈值需在 0-1 之间')
    expect(messages('tool_call', defaultConfig('tool_call'))).toContain('工具调用必须选择工具')
  })

  it('condition / loop / parallel', () => {
    const condition = messages('condition', defaultConfig('condition'))
    expect(condition).toContain('第 1 个分支名称不能为空')
    expect(condition).toContain('分支 第 1 个分支 的表达式不能为空')
    expect(condition).toContain('分支 第 1 个分支 必须选择目标节点')
    expect(condition).toContain('必须配置默认分支')
    expect(messages('condition', { branches: [], defaultTarget: '' })).toContain('条件节点至少需要一个分支')

    const loop = messages('loop', { ...defaultConfig('loop'), maxIterations: 0 })
    expect(loop).toContain('必须填写继续条件表达式')
    expect(loop).toContain('最大次数需为 1-100 的整数')
    expect(loop).toContain('必须选择循环体入口')
    expect(loop).toContain('必须选择退出目标')

    const parallel = messages('parallel', defaultConfig('parallel'))
    expect(parallel).toEqual(
      expect.arrayContaining([
        '第 1 个分支名称不能为空',
        '分支 第 1 个分支 必须选择目标节点',
        '第 2 个分支名称不能为空',
        '分支 第 2 个分支 必须选择目标节点',
        '必须选择汇聚目标',
      ]),
    )
    expect(
      messages('parallel', {
        joinStrategy: 'all_success',
        branches: [{ label: 'A', target: 'a' }],
        joinTarget: 'j',
      }),
    ).toContain('分支数需为 2-10 个')
  })

  it('wait / subgraph / human_approval', () => {
    expect(messages('wait', { waitType: 'duration', durationSeconds: 0 })).toContain(
      '等待时长需为 1-3600 秒的整数',
    )
    expect(messages('subgraph', defaultConfig('subgraph'))).toContain('必须选择引用的已保存子图')
    expect(
      messages('subgraph', { graphId: 'g-1', inputs: { order_id: '  ' } }),
    ).toContain('入参 order_id 的映射值不能为空')
    const approval = messages('human_approval', defaultConfig('human_approval'))
    expect(approval).toContain('审批说明必填')
    expect(approval).toContain('必须选择通过目标')
    expect(approval).toContain('必须选择拒绝目标')
  })

  it('valid configs produce no field diagnostics', () => {
    expect(
      fields('condition', {
        branches: [{ label: '大额', expression: '{{trigger-1.context.payload.amount}} > 1000', target: 'tool-human' }],
        defaultTarget: 'tool-auto',
      }),
    ).toEqual([])
    expect(fields('wait')).toEqual([])
  })
})

describe('covered:false hand cross-field rules (U37③)', () => {
  it('condition duplicate labels/targets, expression syntax and default collision', () => {
    const diagnostics = fields('condition', {
      branches: [
        { label: 'x', expression: 'amount >', target: 'a' },
        { label: 'x', expression: '{{ok}} == null', target: 'a' },
      ],
      defaultTarget: 'a',
    })
    const codes = new Set(diagnostics.map((d) => d.code))
    expect(codes.has(FIELD_CODES.BRANCH_LABEL_DUPLICATE)).toBe(true)
    expect(codes.has(FIELD_CODES.BRANCH_TARGET_DUPLICATE)).toBe(true)
    expect(codes.has(FIELD_CODES.EXPRESSION_SYNTAX)).toBe(true)
    expect(codes.has(FIELD_CODES.DEFAULT_TARGET_COLLISION)).toBe(true)
    const dupLabel = diagnostics.find((d) => d.code === FIELD_CODES.BRANCH_LABEL_DUPLICATE)
    expect(dupLabel?.loc.pointer).toBe('/branches/1/label')
    expect(dupLabel?.message).toBe('分支名称重复：x')
  })

  it('loop expression syntax and body/exit collision', () => {
    const diagnostics = fields('loop', {
      mode: 'while',
      continueExpression: 'index >',
      maxIterations: 10,
      bodyTarget: 'same',
      exitTarget: 'same',
    })
    const codes = new Set(diagnostics.map((d) => d.code))
    expect(codes.has(FIELD_CODES.EXPRESSION_SYNTAX)).toBe(true)
    expect(codes.has(FIELD_CODES.LOOP_TARGET_COLLISION)).toBe(true)
  })

  it('foreach items expression syntax and itemName identifier (docs/45)', () => {
    const diagnostics = fields('loop', {
      mode: 'foreach',
      itemsExpression: '{{global.ids} + 1',
      itemName: '1bad',
      bodyTarget: 'tool-body',
      exitTarget: 'tool-exit',
    })
    const byPointer = new Map(diagnostics.map((d) => [d.loc.pointer, d]))
    expect(byPointer.get('/itemsExpression')?.code).toBe(FIELD_CODES.EXPRESSION_SYNTAX)
    expect(byPointer.get('/itemName')?.code).toBe(FIELD_CODES.EXPRESSION_SYNTAX)
  })

  it('foreach valid expression and identifier yields no field diagnostics (docs/45)', () => {
    const diagnostics = fields('loop', {
      mode: 'foreach',
      itemsExpression: '{{global.ids}}',
      itemName: 'order_id',
      collectTarget: 'tool-body',
      bodyTarget: 'tool-body',
      exitTarget: 'tool-exit',
    })
    expect(diagnostics).toEqual([])
  })

  it('parallel duplicates and join collision', () => {
    const diagnostics = fields('parallel', {
      joinStrategy: 'all_success',
      branches: [
        { label: '同', target: 'tool-x' },
        { label: '同', target: 'tool-x' },
      ],
      joinTarget: 'tool-x',
    })
    const byCode = new Map(diagnostics.map((d) => [d.code, d]))
    expect(byCode.get(FIELD_CODES.BRANCH_LABEL_DUPLICATE)?.message).toBe('分支名称重复：同')
    expect(byCode.get(FIELD_CODES.BRANCH_TARGET_DUPLICATE)?.loc.pointer).toBe('/branches/1/target')
    expect(byCode.get(FIELD_CODES.JOIN_TARGET_COLLISION)?.message).toBe('汇聚目标不能与任一分支目标相同')
  })

  it('subgraph empty/duplicate input keys', () => {
    const emptyKey = fields('subgraph', { graphId: 'g-1', inputs: { '': '{{trigger-1.x}}' } })
    expect(emptyKey.map((d) => d.code)).toContain(FIELD_CODES.INPUT_KEY_EMPTY)
    expect(emptyKey.find((d) => d.code === FIELD_CODES.INPUT_KEY_EMPTY)?.loc.pointer).toBe('/inputs')
    const dupKey = fields('subgraph', { graphId: 'g-1', inputs: { a: '{{x.y}}' } })
    // 同键重复在 JS 对象上会被折叠，空键用例已覆盖 pointer；此处只确认不报错。
    expect(dupKey.every((d) => d.code !== FIELD_CODES.INPUT_KEY_EMPTY)).toBe(true)
  })

  it('human_approval target collision', () => {
    const diagnostics = fields('human_approval', {
      ...defaultConfig('human_approval'),
      summary: '审批',
      approvedTarget: 'same',
      rejectedTarget: 'same',
    })
    expect(diagnostics.map((d) => d.code)).toContain(FIELD_CODES.APPROVAL_TARGET_COLLISION)
  })
})

describe('validateParamFields（M3 工具 params 表单化）', () => {
  const schema: MetaSchema = {
    type: 'object',
    properties: {
      sql: { type: 'string', pattern: '\\S' },
      channel: { type: 'string', enum: ['email', 'sms'] },
      limit: { type: 'integer', minimum: 1, maximum: 1000 },
      headers: { type: 'object', additionalProperties: { type: 'string' } },
    },
    required: ['sql', 'channel'],
  }

  it('必填缺失报在该字段 pointer 上，中文走兜底文案', () => {
    const diagnostics = validateParamFields(schema, { channel: 'email' })
    expect(diagnostics.map((d) => [d.loc.pointer, d.code, d.message])).toEqual([
      ['/sql', FIELD_CODES.REQUIRED, '该字段必填'],
    ])
  })

  it('enum 与数值区间错按 pointer 报出', () => {
    const diagnostics = validateParamFields(schema, {
      sql: 'SELECT 1',
      channel: 'wechat',
      limit: 5000,
    })
    expect(diagnostics.map((d) => [d.loc.pointer, d.code])).toEqual([
      ['/channel', FIELD_CODES.ENUM],
      ['/limit', FIELD_CODES.RANGE],
    ])
  })

  it('嵌套 additionalProperties 值 schema 生效（http headers 形态）', () => {
    const diagnostics = validateParamFields(schema, {
      sql: 'SELECT 1',
      channel: 'email',
      headers: { 'X-Token': 123 },
    })
    expect(diagnostics.map((d) => [d.loc.pointer, d.code])).toEqual([
      ['/headers/X-Token', FIELD_CODES.TYPE],
    ])
  })

  it('合法参数无诊断；诊断恒为 layer:field / severity:error', () => {
    expect(validateParamFields(schema, { sql: 'SELECT 1', channel: 'email', limit: 50 })).toEqual([])
    const [first] = validateParamFields(schema, {})
    expect(first.layer).toBe('field')
    expect(first.severity).toBe('error')
    expect(first.loc.nodeId).toBeUndefined()
  })
})

describe('condition LLM 语义模式（D14，docs/48）', () => {
  const semanticConfig = (overrides: Partial<NodeConfig> = {}): NodeConfig => ({
    conditionMode: 'llm',
    branches: [
      { label: '投诉', description: '客户强烈不满', target: 'tool-a' },
      { label: '咨询', description: '客户平和询问', target: 'tool-b' },
    ],
    defaultTarget: 'tool-default',
    ...overrides,
  })

  it('合法语义配置无诊断', () => {
    expect(fields('condition', semanticConfig())).toEqual([])
  })

  it('description 缺失/超长报错，不再校验 expression', () => {
    const config = semanticConfig({
      branches: [
        { label: '投诉', description: '', target: 'tool-a' },
        { label: '咨询', description: '描'.repeat(301), target: 'tool-b' },
      ],
    })
    const pointerList = pointers('condition', config)
    expect(pointerList).toContain('/branches/0/description')
    expect(pointerList).toContain('/branches/1/description')
    expect(pointerList).not.toContain('/branches/0/expression')
  })

  it('LLM 分支填写 expression 报错', () => {
    const config = semanticConfig({
      branches: [
        { label: '投诉', description: '客户强烈不满', expression: '{{x}} > 1', target: 'tool-a' },
      ],
    })
    const diagnostics = fields('condition', config)
    expect(diagnostics.map((d) => d.loc.pointer)).toContain('/branches/0/expression')
  })

  it('classifierPrompt 超长报错', () => {
    const diagnostics = fields('condition', semanticConfig({ classifierPrompt: '要'.repeat(501) }))
    expect(diagnostics.map((d) => d.loc.pointer)).toEqual(['/classifierPrompt'])
  })

  it('缺省 rule 模式继续按 expression 校验（零回归）', () => {
    const config: NodeConfig = {
      branches: [{ label: '大额', expression: '', target: 'tool-a' }],
      defaultTarget: 'tool-default',
    }
    const pointerList = pointers('condition', config)
    expect(pointerList).toContain('/branches/0/expression')
  })
})


describe('wait jitter and multi-event fan-in L1 (docs/54)', () => {
  it('accepts duration jitterSeconds within 0-300 (static only)', () => {
    expect(fields('wait', { waitType: 'duration', durationSeconds: 10, jitterSeconds: 0 })).toEqual([])
    expect(fields('wait', { waitType: 'duration', durationSeconds: 10, jitterSeconds: 300 })).toEqual([])
  })

  it('flags out-of-range or non-integer jitterSeconds', () => {
    for (const jitterSeconds of [301, -1, 1.5]) {
      const diagnostics = fields('wait', { waitType: 'duration', durationSeconds: 10, jitterSeconds })
      expect(diagnostics.map((d) => d.loc.pointer)).toContain('/jitterSeconds')
      expect(pointers('wait', { waitType: 'duration', durationSeconds: 10, jitterSeconds })).toContain('/jitterSeconds')
    }
  })

  it('accepts a complete multi-event fan-in config (1-8 keys)', () => {
    expect(
      fields('wait', { waitType: 'event', eventKeys: ['order_paid', 'order_cancelled'], timeoutSeconds: 30 }),
    ).toEqual([])
    expect(
      fields('wait', {
        waitType: 'event',
        eventKeys: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'],
        timeoutSeconds: 30,
      }),
    ).toEqual([])
  })

  it('requires eventKey or eventKeys (one of the two)', () => {
    const diagnostics = fields('wait', { waitType: 'event', timeoutSeconds: 30 })
    expect(diagnostics.map((d) => d.loc.pointer)).toContain('/eventKey')
  })

  it('flags too many / empty eventKeys', () => {
    const tooMany = fields('wait', {
      waitType: 'event',
      eventKeys: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i'],
      timeoutSeconds: 30,
    })
    expect(tooMany.some((d) => d.loc.pointer === '/eventKeys' && d.code === FIELD_CODES.LENGTH)).toBe(true)
    const empty = fields('wait', { waitType: 'event', eventKeys: [], timeoutSeconds: 30 })
    expect(empty.some((d) => d.loc.pointer === '/eventKeys')).toBe(true)
  })

  it('flags invalid, blank and duplicate eventKeys entries by index', () => {
    const bad = fields('wait', { waitType: 'event', eventKeys: ['ok', 'bad key'], timeoutSeconds: 30 })
    expect(bad.map((d) => d.loc.pointer)).toContain('/eventKeys/1')
    const blank = fields('wait', { waitType: 'event', eventKeys: ['ok', ''], timeoutSeconds: 30 })
    expect(blank.map((d) => d.loc.pointer)).toContain('/eventKeys/1')
    const dup = fields('wait', { waitType: 'event', eventKeys: ['a', 'a'], timeoutSeconds: 30 })
    expect(dup.some((d) => d.code === FIELD_CODES.INPUT_KEY_DUPLICATE)).toBe(true)
  })

  it('flags eventKey + eventKeys used together (mutually exclusive)', () => {
    const diagnostics = fields('wait', {
      waitType: 'event',
      eventKey: 'order_paid',
      eventKeys: ['order_paid', 'other'],
      timeoutSeconds: 30,
    })
    expect(diagnostics.some((d) => d.loc.pointer === '/eventKeys')).toBe(true)
  })
})
