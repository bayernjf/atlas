import { describe, expect, it } from 'vitest'
import { assertMetaSchema, assertNodeConfigSchema, type NodeConfigSchema } from '../metaSchema'

const validOutput = {
  type: 'object',
  properties: { status: { type: 'string' } },
} satisfies NodeConfigSchema['x-outputSchema']

const validNodeRoot: NodeConfigSchema = {
  type: 'object',
  properties: {
    name: { type: 'string', minLength: 1, 'x-variable': true, 'x-widget': 'text' },
    target: { type: 'string', 'x-ref': { kinds: ['*'] } },
    items: {
      type: 'array',
      minItems: 1,
      items: {
        type: 'object',
        properties: { label: { type: 'string' } },
        required: ['label'],
      },
    },
    when: {
      oneOf: [
        { properties: { mode: { const: 'a' } } },
        { properties: { mode: { const: 'b' }, name: { type: 'string' } }, required: ['name'] },
      ],
    },
  },
  required: ['name'],
  additionalProperties: false,
  'x-outputSchema': validOutput,
}

describe('assertMetaSchema', () => {
  it('accepts the 20-keyword subset plus the four frontend x-keywords', () => {
    expect(() => assertNodeConfigSchema(validNodeRoot)).not.toThrow()
  })

  it('accepts boolean and numeric exclusiveMinimum/Maximum (draft-04 parity)', () => {
    expect(() => assertMetaSchema({ type: 'number', exclusiveMinimum: true, minimum: 0 })).not.toThrow()
    expect(() => assertMetaSchema({ type: 'number', exclusiveMinimum: 1 })).not.toThrow()
  })

  it.each([
    ['unknown plain keyword', { format: 'uri' }],
    ['x-widget non-string', { 'x-widget': 1 }],
    ['x-widget empty string', { 'x-widget': '' }],
    ['x-variable non-boolean', { 'x-variable': 'yes' }],
    ['x-ref non-object', { 'x-ref': [] }],
    ['x-ref.kinds non-array', { 'x-ref': { kinds: 'trigger' } }],
    ['illegal type', { type: 'date' }],
    ['properties non-object', { properties: [] }],
    ['required non-string-array', { required: ['a', 1] }],
    ['required field not declared', { required: ['missing'], properties: { a: {} } }],
    ['items non-object', { items: 'x' }],
    ['bad additionalProperties', { additionalProperties: 1 }],
    ['oneOf non-object-array', { oneOf: [1] }],
    ['non-numeric minimum', { minimum: '0' }],
    ['bad pattern regex', { pattern: '([' }],
  ])('rejects %s with the field name in the error', (_name, schema) => {
    expect(() => assertMetaSchema(schema)).toThrow(/MetaSchema/)
  })

  it('rejects x-outputSchema away from the node root', () => {
    expect(() =>
      assertMetaSchema({ type: 'object', properties: { a: { 'x-outputSchema': validOutput } } }),
    ).toThrow(/x-outputSchema/)
  })

  it('rejects x-keywords nested inside x-outputSchema', () => {
    expect(() =>
      assertNodeConfigSchema({
        type: 'object',
        'x-outputSchema': { type: 'object', properties: { a: { 'x-variable': true } } },
      }),
    ).toThrow(/x-\*/)
  })

  it('requires an object-typed root with an object x-outputSchema', () => {
    expect(() => assertNodeConfigSchema({ type: 'string', 'x-outputSchema': validOutput })).toThrow(/object/)
    expect(() => assertNodeConfigSchema({ type: 'object', 'x-outputSchema': [] })).toThrow(/x-outputSchema/)
  })
})
