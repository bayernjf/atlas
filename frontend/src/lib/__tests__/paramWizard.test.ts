import { describe, expect, it } from 'vitest'
import {
  fieldsFromInputs,
  initialDraft,
  kindOfValue,
  overrideFromFields,
  type ParamField,
} from '../paramWizard'

describe('kindOfValue', () => {
  it('maps scalar and composite values to field kinds', () => {
    expect(kindOfValue('a')).toBe('string')
    expect(kindOfValue(7)).toBe('number')
    expect(kindOfValue(true)).toBe('boolean')
    expect(kindOfValue({ a: 1 })).toBe('json')
    expect(kindOfValue([1, 2])).toBe('json')
    expect(kindOfValue(null)).toBe('json')
  })
})

describe('fieldsFromInputs', () => {
  it('expands each top-level key with its initial draft, keeping order', () => {
    const fields = fieldsFromInputs({
      orderId: 'A-1',
      amount: 99.5,
      vip: false,
      items: [{ sku: 's1' }],
    })
    expect(fields.map((f) => f.key)).toEqual(['orderId', 'amount', 'vip', 'items'])
    expect(fields.map((f) => f.kind)).toEqual(['string', 'number', 'boolean', 'json'])
    expect(fields[0].draft).toBe('A-1')
    expect(fields[1].draft).toBe('99.5')
    expect(fields[2].draft).toBe('false')
    expect(fields[3].draft).toBe('[{"sku":"s1"}]')
  })

  it('returns no fields for empty inputs', () => {
    expect(fieldsFromInputs({})).toEqual([])
  })

  it('renders composite initial drafts via JSON.stringify', () => {
    expect(initialDraft('json', { a: 1 })).toBe('{"a":1}')
  })
})

describe('overrideFromFields', () => {
  it('round-trips every kind', () => {
    const result = overrideFromFields([
      { key: 's', kind: 'string', draft: 'hello' },
      { key: 'n', kind: 'number', draft: '42' },
      { key: 'b', kind: 'boolean', draft: 'true' },
      { key: 'o', kind: 'json', draft: '{"x":1}' },
    ])
    expect(result).toEqual({
      ok: true,
      value: { s: 'hello', n: 42, b: true, o: { x: 1 } },
    })
  })

  it('omits blank fields, including whitespace-only strings', () => {
    const result = overrideFromFields([
      { key: 's', kind: 'string', draft: '   ' },
      { key: 'n', kind: 'number', draft: '' },
      { key: 'o', kind: 'json', draft: '' },
    ])
    expect(result.ok).toBe(true)
    expect(result.value).toEqual({})
  })

  it('always includes boolean fields using the switch state', () => {
    const result = overrideFromFields([
      { key: 'b', kind: 'boolean', draft: 'false' },
    ])
    expect(result.value).toEqual({ b: false })
  })

  it('rejects non-numeric number fields with the offending key', () => {
    const result = overrideFromFields([
      { key: 'n', kind: 'number', draft: 'abc' },
    ])
    expect(result).toEqual({ ok: false, errorKey: 'n', errorCode: 'not_a_number' })
  })

  it('rejects malformed json fields with the offending key', () => {
    const result = overrideFromFields([
      { key: 'o', kind: 'json', draft: '{nope}' },
    ])
    expect(result).toEqual({ ok: false, errorKey: 'o', errorCode: 'invalid_json' })
  })

  it('accepts changed fields as a ParamField list', () => {
    const fields: ParamField[] = fieldsFromInputs({ amount: 1 })
    fields[0].draft = '2'
    expect(overrideFromFields(fields).value).toEqual({ amount: 2 })
  })
})
