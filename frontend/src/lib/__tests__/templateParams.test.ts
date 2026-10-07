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
