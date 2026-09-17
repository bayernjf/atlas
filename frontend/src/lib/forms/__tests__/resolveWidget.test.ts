import { describe, expect, it } from 'vitest'
import { resolveWidget } from '../resolveWidget'
import type { MetaSchema } from '../../schemas/metaSchema'

function schema(partial: MetaSchema): MetaSchema {
  return partial
}

describe('resolveWidget 选择序（U39①）', () => {
  it('节点 schema 的 x-widget 最优先', () => {
    const result = resolveWidget(schema({ type: 'string', 'x-widget': 'textarea' }), 'node')
    expect(result).toEqual({ kind: 'widget', widget: 'textarea' })
  })

  it('工具来源忽略 x-widget（Capability 拒绝一切 x-*）', () => {
    const result = resolveWidget(schema({ type: 'string', 'x-widget': 'textarea' }), 'tool')
    expect(result).toEqual({ kind: 'widget', widget: 'text' })
  })

  it('enum/const 解析为 select', () => {
    expect(resolveWidget(schema({ enum: ['a', 'b'] }))).toEqual({ kind: 'widget', widget: 'select' })
    expect(resolveWidget(schema({ const: 'fixed' }))).toEqual({ kind: 'widget', widget: 'select' })
  })

  it('boolean 解析为 switch', () => {
    expect(resolveWidget(schema({ type: 'boolean' }))).toEqual({ kind: 'widget', widget: 'switch' })
  })

  it('integer/number 解析为 number', () => {
    expect(resolveWidget(schema({ type: 'integer' }))).toEqual({ kind: 'widget', widget: 'number' })
    expect(resolveWidget(schema({ type: 'number' }))).toEqual({ kind: 'widget', widget: 'number' })
  })

  it('带 properties 的 object 解析为递归分组', () => {
    expect(resolveWidget(schema({ type: 'object', properties: { a: { type: 'string' } } }))).toEqual({
      kind: 'group',
    })
  })

  it('无 type 但带 properties 也解析为分组', () => {
    expect(resolveWidget(schema({ properties: { a: { type: 'string' } } }))).toEqual({ kind: 'group' })
  })

  it('仅 additionalProperties 的 object 解析为键值行', () => {
    expect(resolveWidget(schema({ type: 'object', additionalProperties: { type: 'string' } }))).toEqual({
      kind: 'keyvalue',
    })
    expect(resolveWidget(schema({ type: 'object', additionalProperties: true }))).toEqual({
      kind: 'keyvalue',
    })
  })

  it('带 items 的 array 解析为增删行，缺 items 降级 json', () => {
    expect(resolveWidget(schema({ type: 'array', items: { type: 'string' } }))).toEqual({ kind: 'array' })
    expect(resolveWidget(schema({ type: 'array' }))).toEqual({ kind: 'widget', widget: 'json' })
  })

  it('string 默认 text，x-variable 字段走 variable-input', () => {
    expect(resolveWidget(schema({ type: 'string' }))).toEqual({ kind: 'widget', widget: 'text' })
    expect(resolveWidget(schema({ type: 'string', 'x-variable': true }))).toEqual({
      kind: 'widget',
      widget: 'variable-input',
    })
  })
})

describe('resolveWidget 降级序（U39①）', () => {
  it('无统一 properties 的 oneOf 联合降级 json（message/send to：string|array）', () => {
    const to = schema({
      oneOf: [
        { type: 'string' },
        { type: 'array', items: { type: 'string' }, maxItems: 20 },
      ],
    })
    expect(resolveWidget(to)).toEqual({ kind: 'widget', widget: 'json' })
  })

  it('object + properties + 顶层判别 oneOf 仍按 properties 展开为 group（trigger 三分支）', () => {
    const trigger = schema({
      type: 'object',
      properties: {
        triggerType: { type: 'string', enum: ['manual', 'schedule', 'webhook'] },
        cron: { type: 'string' },
        webhookUrl: { type: 'string' },
      },
      required: ['triggerType'],
      oneOf: [
        { properties: { triggerType: { const: 'manual' } } },
        { properties: { triggerType: { const: 'schedule' } }, required: ['cron'] },
        { properties: { triggerType: { const: 'webhook' } }, required: ['webhookUrl'] },
      ],
    })
    expect(resolveWidget(trigger)).toEqual({ kind: 'group' })
  })

  it('空 schema {} 降级 json', () => {
    expect(resolveWidget(schema({}))).toEqual({ kind: 'widget', widget: 'json' })
  })

  it('无 type 无 properties 降级 json（database params / http body / timeout 形态）', () => {
    expect(resolveWidget(schema({ description: '任意' }))).toEqual({ kind: 'widget', widget: 'json' })
  })

  it('空 properties 且无 additionalProperties 的 object 降级 json（shop/list_pending_refunds 根）', () => {
    expect(resolveWidget(schema({ type: 'object', properties: {} }))).toEqual({
      kind: 'widget',
      widget: 'json',
    })
  })

  it('null type 降级 json', () => {
    expect(resolveWidget(schema({ type: 'null' }))).toEqual({ kind: 'widget', widget: 'json' })
  })
})
