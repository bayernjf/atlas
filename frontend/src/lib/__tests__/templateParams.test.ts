import { describe, expect, it } from 'vitest'
import {
  buildInitialValues,
  buildParamFields,
  validateParamValues,
  type ParamField,
} from '../templateParams'
import type { TemplateParams } from '../apiClient'

const PARAMS: TemplateParams = {
  min_amount: { type: 'number', label: '最小金额', required: true, hint: '低于此金额不退款' },
  channel: { type: 'select', label: '渠道', options: ['email', 'webhook', 'im'], default: 'webhook' },
  notify: { type: 'boolean', label: '是否通知', required: false },
  note: { type: 'string', label: '备注', required: false },
}

describe('buildParamFields (U1188)', () => {
  it('derives one field per declared param with defaults', () => {
    const fields = buildParamFields(PARAMS)
    expect(fields).toHaveLength(4)
    const min = fields.find((f) => f.name === 'min_amount')
    expect(min).toMatchObject({ label: '最小金额', type: 'number', required: true, hint: '低于此金额不退款' })
    const channel = fields.find((f) => f.name === 'channel')
    expect(channel?.options).toEqual(['email', 'webhook', 'im'])
    expect(channel?.type).toBe('select')
    expect(channel?.default).toBe('webhook')
    expect(fields.find((f) => f.name === 'notify')?.required).toBe(false)
  })

  it('falls back label to param name when label missing', () => {
    const fields = buildParamFields({ p: { type: 'string' } })
    expect(fields[0].label).toBe('p')
    expect(fields[0].type).toBe('string')
    expect(fields[0].required).toBe(false)
  })

  it('treats missing params as empty', () => {
    expect(buildParamFields({})).toEqual([])
  })
})

describe('buildInitialValues (U1188)', () => {
  it('uses declaration default, falls back to first select option', () => {
    const fields = buildParamFields(PARAMS)
    const values = buildInitialValues(fields)
    expect(values.channel).toBe('webhook')
    expect(values.min_amount).toBeUndefined()
    expect(values.notify).toBeUndefined()
  })

  it('select without default picks first option', () => {
    const fields = buildParamFields({ mode: { type: 'select', options: ['a', 'b'] } })
    expect(buildInitialValues(fields)).toEqual({ mode: 'a' })
  })
})

describe('validateParamValues (U1188)', () => {
  const fields: ParamField[] = buildParamFields(PARAMS)

  it('accepts complete valid values', () => {
    const errors = validateParamValues(fields, { min_amount: 250, channel: 'im', notify: false, note: 'x' })
    expect(errors).toEqual([])
  })

  it('flags missing required', () => {
    const errors = validateParamValues(fields, {})
    expect(errors.join('；')).toContain('最小金额（min_amount）为必填')
  })

  it('flags wrong number type', () => {
    const errors = validateParamValues(fields, { min_amount: '100' })
    expect(errors.join('；')).toContain('必须是数字')
  })

  it('flags wrong boolean type', () => {
    const errors = validateParamValues(fields, { min_amount: 1, notify: 'yes' })
    expect(errors.join('；')).toContain('必须是布尔值')
  })

  it('flags out-of-range select value', () => {
    const errors = validateParamValues(fields, { min_amount: 1, channel: 'sms' })
    expect(errors.join('；')).toContain('不在可选范围内')
  })
})

// =============================================================================
// 打包 ZX（docs/106 §2.5）：结构化参数纯逻辑——初值/递归校验
// U1251：object/array 初值、递归校验、向后兼容
// =============================================================================
describe('pack ZX structured params logic', () => {
  const structured = buildParamFields({
    webhook: {
      type: 'object',
      required: true,
      properties: {
        url: { type: 'string', required: true },
        secret: { type: 'string' },
      },
    },
    channels: {
      type: 'array',
      minItems: 1,
      items: { type: 'select', options: ['email', 'webhook'] },
    },
  })

  it('builds initial values with empty containers for object/array', () => {
    const values = buildInitialValues(structured)
    expect(values.webhook).toEqual({})
    expect(values.channels).toEqual([])
  })

  it('builds structured default values when declared', () => {
    const values = buildInitialValues(
      buildParamFields({
        cfg: { type: 'object', default: { level: 'info' }, properties: { level: { type: 'string' } } },
        tags: { type: 'array', default: ['a'], items: { type: 'string' } },
      }),
    )
    expect(values.cfg).toEqual({ level: 'info' })
    expect(values.tags).toEqual(['a'])
  })

  it('flags non-object value', () => {
    const errors = validateParamValues(structured, { webhook: 'nope', channels: ['email'] })
    expect(errors.join('；')).toContain('必须是对象')
  })

  it('flags missing nested required subfield', () => {
    const errors = validateParamValues(structured, { webhook: { secret: 's' }, channels: ['email'] })
    expect(errors.join('；')).toContain('webhook.url 为必填')
  })

  it('flags non-array value', () => {
    const errors = validateParamValues(structured, { webhook: { url: 'u' }, channels: 'im' })
    expect(errors.join('；')).toContain('必须是数组')
  })

  it('flags out-of-range array element', () => {
    const errors = validateParamValues(structured, { webhook: { url: 'u' }, channels: ['sms'] })
    expect(errors.join('；')).toContain('不在可选范围内')
  })

  it('flags insufficient minItems', () => {
    const errors = validateParamValues(structured, { webhook: { url: 'u' }, channels: [] })
    expect(errors.join('；')).toContain('至少需要 1 项')
  })

  it('accepts complete structured values', () => {
    const errors = validateParamValues(structured, {
      webhook: { url: 'https://x', secret: 's' },
      channels: ['email', 'webhook'],
    })
    expect(errors).toEqual([])
  })

  it('validates array-of-object elements recursively', () => {
    const fields = buildParamFields({
      rules: {
        type: 'array',
        items: { type: 'object', properties: { min: { type: 'number', required: true } } },
      },
    })
    const bad = validateParamValues(fields, { rules: [{ min: 'x' }, {}] })
    expect(bad.join('；')).toContain('rules[0].min')
    expect(bad.join('；')).toContain('rules[1].min 为必填')
    const ok = validateParamValues(fields, { rules: [{ min: 1 }, { min: 2 }] })
    expect(ok).toEqual([])
  })

  it('keeps scalar validation unchanged (regression)', () => {
    const fields = buildParamFields({
      p: { type: 'string' },
      n: { type: 'number', required: true },
      b: { type: 'boolean' },
      s: { type: 'select', options: ['a', 'b'] },
    })
    expect(validateParamValues(fields, { n: 1, b: true, s: 'a' })).toEqual([])
    expect(validateParamValues(fields, {}).join('；')).toContain('为必填')
  })
})
