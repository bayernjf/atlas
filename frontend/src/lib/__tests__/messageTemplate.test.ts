import { describe, expect, it } from 'vitest'
import {
  MESSAGE_TEMPLATE_KINDS,
  MAX_VARIABLES,
  extractPlaceholders,
  parseVariablesInput,
  validateMessageTemplate,
  validateVariables,
} from '../messageTemplate'

describe('messageTemplate pure logic (pack A2, docs/98; U1195)', () => {
  it('extractPlaceholders dedups and trims whitespace', () => {
    expect(extractPlaceholders('{{a}} x {{ b }} {{a}}')).toEqual(['a', 'b'])
    expect(extractPlaceholders('无占位')).toEqual([])
    expect(extractPlaceholders('{{}} {{ }}')).toEqual([])
  })

  it('parseVariablesInput splits on comma/newline/space and dedups', () => {
    expect(parseVariablesInput('a, b\n c， d、 a')).toEqual(['a', 'b', 'c', 'd'])
    expect(parseVariablesInput('   ')).toEqual([])
  })

  it('validateVariables rejects illegal names, duplicates and overflow', () => {
    expect(validateVariables(['ok', 'ok'])).toEqual(['变量名重复：ok'])
    expect(validateVariables(['1bad']).some((e) => e.includes('不合法'))).toBe(true)
    expect(validateVariables(['a-b']).some((e) => e.includes('不合法'))).toBe(true)
    const overflow = validateVariables(Array.from({ length: MAX_VARIABLES + 1 }, (_, i) => `v${i}`))
    expect(overflow.some((e) => e.includes('不能超过 20'))).toBe(true)
    expect(validateVariables(['a', 'b'])).toEqual([])
  })

  it('validateMessageTemplate enforces shape and declared-placeholder rule', () => {
    const valid = validateMessageTemplate({
      name: '审批提醒',
      kind: 'approval',
      subject: 's {{title}}',
      body: 'b {{title}} {{approver}}',
      variables: ['title', 'approver'],
    })
    expect(valid).toEqual([])

    const undeclared = validateMessageTemplate({
      name: '审批提醒',
      kind: 'approval',
      subject: 's {{title}}',
      body: 'b {{ghost}}',
      variables: ['title'],
    })
    expect(undeclared.some((e) => e.includes('未声明变量 ghost'))).toBe(true)

    expect(validateMessageTemplate({
      name: '',
      kind: 'sms',
      subject: 's',
      body: 'b',
      variables: [],
    }).length).toBeGreaterThan(0)

    expect(validateMessageTemplate({
      name: 'x',
      kind: 'alert',
      subject: 's {{title}}',
      body: 'b {{title}}',
      variables: ['title'],
    })).toEqual([])
  })

  it('kind enum is exactly approval and alert', () => {
    expect(MESSAGE_TEMPLATE_KINDS).toEqual(['approval', 'alert'])
  })
})
