import { describe, expect, it } from 'vitest'
import type { AdapterInfo } from '../../apiClient'
import { buildToolSchemaTable, isFormRenderable } from '../toolSchemas'
import type { MetaSchema } from '../../schemas/metaSchema'

const querySchema: MetaSchema = {
  type: 'object',
  properties: { sql: { type: 'string' }, limit: { type: 'integer' } },
  required: ['sql'],
}

const adapters: AdapterInfo[] = [
  {
    id: 'database',
    type: 'database',
    healthy: true,
    tools: [
      {
        name: 'query',
        description: '通用 SQL 只读查询',
        permission: 'read',
        idempotent: true,
        input_schema: querySchema,
        output_schema: { type: 'object' },
      },
    ],
  },
  {
    id: 'shop',
    type: 'shop',
    healthy: false,
    tools: [
      {
        name: 'list_pending_refunds',
        description: '获取待处理退款单列表',
        permission: 'read',
        idempotent: true,
        input_schema: {},
        output_schema: { type: 'object' },
      },
    ],
  },
]

describe('工具 schema 第二来源（U39⑤）', () => {
  it('按 <adapter>/<tool> 建表', () => {
    const table = buildToolSchemaTable(adapters)
    expect(Object.keys(table).sort()).toEqual(['database/query', 'shop/list_pending_refunds'])
  })

  it('持发现快照引用，不复制', () => {
    const table = buildToolSchemaTable(adapters)
    expect(table['database/query']).toBe(adapters[0].tools[0].input_schema)
    expect(table['shop/list_pending_refunds']).toBe(adapters[1].tools[0].input_schema)
  })

  it('未注册工具/未就绪/失败快照取不到 schema', () => {
    const table = buildToolSchemaTable(adapters)
    expect(table['ghost/tool']).toBeUndefined()
    expect(buildToolSchemaTable(null)).toEqual({})
    expect(buildToolSchemaTable(undefined)).toEqual({})
  })

  it('发现里的未健康适配器同样入表（已保存图仍可编辑其参数）', () => {
    expect(buildToolSchemaTable(adapters)['shop/list_pending_refunds']).toBeDefined()
  })
})

describe('isFormRenderable 降级判据（U39③）', () => {
  it('对象根 schema 可表单化', () => {
    expect(isFormRenderable(querySchema)).toBe(true)
    expect(isFormRenderable({ type: 'object', additionalProperties: { type: 'string' } })).toBe(true)
  })

  it('空 schema（shop/list_pending_refunds）与空对象都不可表单化', () => {
    expect(isFormRenderable({})).toBe(false)
    expect(isFormRenderable({ type: 'object', properties: {} })).toBe(false)
  })

  it('无 type 无 properties、oneOf、数组/标量根均降级', () => {
    expect(isFormRenderable({ description: '无类型' })).toBe(false)
    expect(isFormRenderable({ oneOf: [{ type: 'string' }, { type: 'array' }] })).toBe(false)
    expect(isFormRenderable({ type: 'array', items: { type: 'string' } })).toBe(false)
    expect(isFormRenderable({ type: 'string' })).toBe(false)
  })

  it('缺 schema 不可表单化（未注册工具/发现失败）', () => {
    expect(isFormRenderable(null)).toBe(false)
    expect(isFormRenderable(undefined)).toBe(false)
  })
})
