import { describe, expect, it } from 'vitest'
import {
  templateParamsToMetaSchema,
  templateParamsToUiSchema,
} from '../templateParamSchema'
import { buildFormTree, type FormWidgetNode } from '../formTree'
import { applyUiSchema, decorateNodeForRender, type UiSchema } from '../uiSchema'
import type { TemplateParams } from '../../apiClient'

const PARAMS: TemplateParams = {
  min_amount: { type: 'number', label: '最小金额', required: true, hint: '低于此金额不退款' },
  channel: { type: 'select', label: '渠道', options: ['email', 'webhook', 'im'], default: 'webhook' },
  notify: { type: 'boolean', label: '是否通知', required: false },
  note: { type: 'string', label: '备注', required: false },
}

describe('templateParamsToMetaSchema (U1245)', () => {
  it('maps four param types with required/default/enum', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    expect(schema.type).toBe('object')
    expect(schema.required).toEqual(['min_amount'])
    expect(schema.properties?.min_amount).toEqual({ type: 'number' })
    expect(schema.properties?.channel).toEqual({
      type: 'string',
      enum: ['email', 'webhook', 'im'],
      default: 'webhook',
    })
    expect(schema.properties?.notify).toEqual({ type: 'boolean' })
    expect(schema.properties?.note).toEqual({ type: 'string' })
  })

  it('returns empty object schema for undefined/empty params', () => {
    expect(templateParamsToMetaSchema(undefined)).toEqual({ type: 'object', properties: {} })
    expect(templateParamsToMetaSchema({})).toEqual({ type: 'object', properties: {} })
  })

  it('omits required when none declared and degrades select without options', () => {
    const schema = templateParamsToMetaSchema({
      free: { type: 'select', label: '无选项' },
    })
    expect(schema.required).toBeUndefined()
    expect(schema.properties?.free).toEqual({ type: 'string' })
  })
})

describe('templateParamsToUiSchema (U1246)', () => {
  it('carries label/hint into labels/hints slots', () => {
    const ui = templateParamsToUiSchema(PARAMS)
    expect(ui.labels).toMatchObject({ min_amount: '最小金额', channel: '渠道', note: '备注' })
    expect(ui.hints).toEqual({ min_amount: '低于此金额不退款' })
  })

  it('returns empty uiSchema when no label/hint declared', () => {
    expect(templateParamsToUiSchema({ x: { type: 'string' } })).toEqual({})
  })
})

describe('UiSchema hints slot rendering contract (U1247)', () => {
  it('bakes hint into widget node via decorateNodeForRender', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    const tree = buildFormTree(schema, { min_amount: 100 }, { source: 'tool' })
    expect(tree.kind).toBe('group')
    if (tree.kind !== 'group') return
    const minNode = tree.children.find((c) => c.path.at(-1) === 'min_amount')
    expect(minNode?.kind).toBe('widget')
    if (minNode?.kind !== 'widget') return
    const decorated = decorateNodeForRender(minNode, templateParamsToUiSchema(PARAMS))
    expect(decorated.kind).toBe('widget')
    if (decorated.kind !== 'widget') return
    expect(decorated.hint).toBe('低于此金额不退款')
    expect(decorated.label).toBe('最小金额')
  })

  it('leaves hint undefined for fields without hint', () => {
    const ui: UiSchema = { labels: { note: '备注' } }
    const tree = buildFormTree(templateParamsToMetaSchema(PARAMS), { note: 'x' }, { source: 'tool' })
    expect(tree.kind).toBe('group')
    if (tree.kind !== 'group') return
    const noteNode = tree.children.find((c) => c.path.at(-1) === 'note')
    expect(noteNode?.kind).toBe('widget')
    if (noteNode?.kind !== 'widget') return
    const decorated = decorateNodeForRender(noteNode, ui)
    expect(decorated.kind).toBe('widget')
    if (decorated.kind !== 'widget') return
    expect(decorated.hint).toBeUndefined()
  })

  it('applyUiSchema passes through with hints untouched when absent', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    const tree = buildFormTree(schema, { min_amount: 100 }, { source: 'tool' })
    const applied = applyUiSchema(tree, { min_amount: 100 })
    expect(applied).toBe(tree)
  })
})

describe('template param wizard schema-driven contract (U1248)', () => {
  it('derives one widget field per declared param', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    const tree = buildFormTree(schema, { min_amount: 100, channel: 'webhook', notify: true, note: 'x' }, { source: 'tool' })
    expect(tree.kind).toBe('group')
    if (tree.kind !== 'group') return
    const widgetNames = tree.children
      .filter((c): c is FormWidgetNode => c.kind === 'widget')
      .map((c) => c.widget)
    expect(widgetNames).toContain('number')
    expect(widgetNames).toContain('select')
    expect(widgetNames).toContain('switch')
    // source='tool' 时 string 按 M0 工具表单语义升级为 variable-input（无 scope 退化 TextArea，不崩）。
    expect(widgetNames).toContain('variable-input')
  })
})

// =============================================================================
// 打包 ZX（docs/106 §2.3）：模板参数声明面结构化——嵌套 object / 数组 / 条件显隐
// U1251：MetaSchema 递归——object → group、array → items+min/max
// =============================================================================
describe('pack ZX structured declarations', () => {
  it('maps nested object to recursive properties with local required', () => {
    const schema = templateParamsToMetaSchema({
      webhook: {
        type: 'object',
        required: true,
        properties: {
          url: { type: 'string', required: true },
          secret: { type: 'string' },
        },
      },
    })
    expect(schema.type).toBe('object')
    expect(schema.required).toEqual(['webhook'])
    const webhook = schema.properties!['webhook']
    expect(webhook.type).toBe('object')
    expect(webhook.required).toEqual(['url'])
    expect(webhook.properties!['url'].type).toBe('string')
    expect(webhook.properties!['secret'].type).toBe('string')
  })

  it('maps array to items with min/max passthrough', () => {
    const schema = templateParamsToMetaSchema({
      channels: {
        type: 'array',
        minItems: 1,
        maxItems: 3,
        items: { type: 'select', options: ['email', 'webhook'] },
      },
    })
    const channels = schema.properties!['channels']
    expect(channels.type).toBe('array')
    expect(channels.minItems).toBe(1)
    expect(channels.maxItems).toBe(3)
    expect(channels.items!.type).toBe('string')
    expect(channels.items!.enum).toEqual(['email', 'webhook'])
  })

  it('maps array of object to nested items group', () => {
    const schema = templateParamsToMetaSchema({
      rules: {
        type: 'array',
        items: {
          type: 'object',
          properties: { min: { type: 'number', required: true }, max: { type: 'number' } },
        },
      },
    })
    const rules = schema.properties!['rules']
    expect(rules.items!.type).toBe('object')
    expect(rules.items!.required).toEqual(['min'])
    expect(rules.items!.properties!['max'].type).toBe('number')
  })

  it('passes structured defaults through untouched', () => {
    const schema = templateParamsToMetaSchema({
      cfg: { type: 'object', default: { level: 'info' }, properties: { level: { type: 'string' } } },
      tags: { type: 'array', default: ['a'], items: { type: 'string' } },
    })
    expect(schema.properties!['cfg'].default).toEqual({ level: 'info' })
    expect(schema.properties!['tags'].default).toEqual(['a'])
  })

  it('maps visibleWhen to hiddenWhen (root field)', () => {
    const uiSchema = templateParamsToUiSchema({
      mode: { type: 'select', options: ['auto', 'manual'] },
      manual_reason: {
        type: 'string',
        visibleWhen: { field: 'mode', equals: 'manual' },
      },
    })
    expect(uiSchema.hiddenWhen).toEqual([
      { field: 'mode', equals: 'manual', show: ['manual_reason'] },
    ])
  })

  it('maps nested object visibleWhen to rootScoped hiddenWhen', () => {
    const uiSchema = templateParamsToUiSchema({
      webhook: {
        type: 'object',
        properties: {
          enabled: { type: 'boolean' },
          url: { type: 'string', visibleWhen: { field: 'enabled', equals: true } },
        },
      },
    })
    expect(uiSchema.hiddenWhen).toEqual([
      { field: 'enabled', equals: true, show: ['url'], rootScoped: true },
    ])
  })

  it('recursively collects nested labels and hints with pointer-style keys', () => {
    const uiSchema = templateParamsToUiSchema({
      webhook: {
        type: 'object',
        label: 'Webhook 配置',
        properties: {
          url: { type: 'string', label: 'URL', hint: '回调地址' },
          secret: { type: 'string' },
        },
      },
      channels: {
        type: 'array',
        items: { type: 'object', properties: { name: { type: 'string', label: '渠道名' } } },
      },
    })
    expect(uiSchema.labels!['webhook']).toBe('Webhook 配置')
    expect(uiSchema.labels!['webhook.url']).toBe('URL')
    expect(uiSchema.hints!['webhook.url']).toBe('回调地址')
    expect(uiSchema.labels!['channels[].name']).toBe('渠道名')
  })

  it('keeps scalar mapping unchanged (regression)', () => {
    const schema = templateParamsToMetaSchema({
      p: { type: 'string', default: 'v' },
      n: { type: 'number', required: true },
      b: { type: 'boolean' },
      s: { type: 'select', options: ['a', 'b'] },
    })
    expect(schema.properties!['p']).toEqual({ type: 'string', default: 'v' })
    expect(schema.properties!['n']).toEqual({ type: 'number' })
    expect(schema.properties!['b']).toEqual({ type: 'boolean' })
    expect(schema.properties!['s']).toEqual({ type: 'string', enum: ['a', 'b'] })
    expect(schema.required).toEqual(['n'])
  })
})
