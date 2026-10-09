/**
 * 判别异构数组渲染契约测试（docs/119 §5，2026-10-10）。
 *
 * 覆盖：oneOfDiscriminant 提取（一致/不一致/缺 const/值重复）；formTree 判别数组
 * 四分支匹配；未知判别值/非 object 元素降级 json；defaultValueFor 首个分支默认
 * 对象（判别键=const）；FormRenderer 判别数组渲染（SSR 不崩、判别键只读、添加行默认值）。
 *
 * 判别数组形状与 apiClient RolloutRule（to: internal|lowValueBucket|canary|full）对齐，
 * 但本批只验证内核渲染能力，不迁移 RolloutModal（第 3 步第二片另立批）。
 */
import { describe, expect, it } from 'vitest'
import { renderToString } from 'react-dom/server'
import { createElement } from 'react'
import { FormRenderer } from '../FormRenderer'
import { buildFormTree, defaultValueFor, oneOfDiscriminant } from '../formTree'
import type { MetaSchema } from '../../schemas/metaSchema'

/** 与 RolloutRule 同形的判别数组 items schema（docs/119 §2.1 判别协议）。 */
const ruleItems: MetaSchema = {
  oneOf: [
    {
      type: 'object',
      properties: { to: { const: 'internal' }, tenants: { type: 'array', items: { type: 'string' } } },
      required: ['to', 'tenants'],
    },
    {
      type: 'object',
      properties: {
        to: { const: 'lowValueBucket' },
        field: { type: 'string' },
        op: { type: 'string' },
        value: { type: 'number' },
        percent: { type: 'number' },
      },
      required: ['to', 'value'],
    },
    {
      type: 'object',
      properties: { to: { const: 'canary' }, percent: { type: 'number' } },
      required: ['to', 'percent'],
    },
    {
      type: 'object',
      properties: { to: { const: 'full' } },
      required: ['to'],
    },
  ],
}

const ruleArraySchema: MetaSchema = { type: 'array', items: ruleItems }

describe('oneOfDiscriminant（docs/119 §2.1）', () => {
  it('一致 const 判别键时提取 key＋branches', () => {
    const disc = oneOfDiscriminant(ruleItems.oneOf!)
    expect(disc).not.toBeNull()
    expect(disc!.key).toBe('to')
    expect([...disc!.branches.keys()]).toEqual(['internal', 'lowValueBucket', 'canary', 'full'])
    expect(disc!.branches.get('internal')!.properties!.tenants).toBeDefined()
  })

  it('分支 const 键不一致 → null', () => {
    const oneOf: MetaSchema[] = [
      { properties: { to: { const: 'a' } } },
      { properties: { kind: { const: 'b' } } },
    ]
    expect(oneOfDiscriminant(oneOf)).toBeNull()
  })

  it('分支缺 const（或带多个 const）→ null', () => {
    expect(oneOfDiscriminant([{ properties: { to: { const: 'a' } } }, { properties: { to: { type: 'string' } } }])).toBeNull()
    expect(
      oneOfDiscriminant([
        { properties: { to: { const: 'a' }, x: { const: 1 } } },
        { properties: { to: { const: 'b' }, x: { const: 2 } } },
      ]),
    ).toBeNull()
  })

  it('const 值重复 → null', () => {
    expect(
      oneOfDiscriminant([
        { properties: { to: { const: 'a' } } },
        { properties: { to: { const: 'a' } } },
      ]),
    ).toBeNull()
  })

  it('constOf 取分支判别值（defaultValueFor 组装用）', () => {
    const disc = oneOfDiscriminant(ruleItems.oneOf!)!
    expect(disc.constOf(ruleItems.oneOf![0])).toBe('internal')
    expect(disc.constOf(ruleItems.oneOf![3])).toBe('full')
  })
})

describe('buildFormTree 判别数组（docs/119 §2.2）', () => {
  const value = [
    { to: 'internal', tenants: ['acme'] },
    { to: 'lowValueBucket', field: 'payload.amount', op: '<=', value: 200, percent: 100 },
    { to: 'canary', percent: 5 },
    { to: 'full' },
  ]

  it('四分支各按 to 值匹配分支子树', () => {
    const tree = buildFormTree(ruleArraySchema, value, { source: 'node' })
    expect(tree.kind).toBe('array')
    const items = tree.kind === 'array' ? tree.items : []
    expect(items).toHaveLength(4)

    const row0 = items[0]
    expect(row0.kind).toBe('group')
    if (row0.kind === 'group') {
      const keys = row0.children.map((child) => child.label)
      expect(keys).toContain('tenants')
      expect(keys).not.toContain('percent')
    }

    const row1 = items[1]
    expect(row1.kind).toBe('group')
    if (row1.kind === 'group') {
      const keys = row1.children.map((child) => child.label)
      expect(keys).toContain('value')
      expect(keys).toContain('percent')
      expect(keys).not.toContain('tenants')
    }

    const row2 = items[2]
    if (row2.kind === 'group') {
      const keys = row2.children.map((child) => child.label)
      expect(keys).toEqual(['to', 'percent'])
    }

    const row3 = items[3]
    if (row3.kind === 'group') {
      const keys = row3.children.map((child) => child.label)
      expect(keys).toEqual(['to'])
    }
  })

  it('判别键字段为 const → widget select（只读单选项）', () => {
    const tree = buildFormTree(ruleArraySchema, [value[0]], { source: 'node' })
    const row = (tree.kind === 'array' ? tree.items : [])[0]
    if (row.kind === 'group') {
      const toField = row.children.find((child) => child.label === 'to')
      expect(toField?.kind).toBe('widget')
      if (toField?.kind === 'widget') {
        expect(toField.widget).toBe('select')
        expect((toField.schema as MetaSchema).const).toBe('internal')
      }
    }
  })

  it('未知 to 值 → 该行降级 json', () => {
    const tree = buildFormTree(ruleArraySchema, [{ to: 'aliens' }], { source: 'node' })
    const row = (tree.kind === 'array' ? tree.items : [])[0]
    expect(row.kind).toBe('widget')
    if (row.kind === 'widget') expect(row.widget).toBe('json')
  })

  it('非 object 元素（判别键缺失）→ 该行降级 json', () => {
    const tree = buildFormTree(ruleArraySchema, ['garbage'], { source: 'node' })
    const row = (tree.kind === 'array' ? tree.items : [])[0]
    expect(row.kind).toBe('widget')
  })
})

describe('defaultValueFor 判别数组（docs/119 §2.3）', () => {
  it('oneOf 返回首个分支默认对象（判别键=const＋字段默认）', () => {
    expect(defaultValueFor(ruleItems)).toEqual({ to: 'internal', tenants: [] })
  })

  it('单分支联合（如仅 full）默认对象', () => {
    const items: MetaSchema = { oneOf: [{ type: 'object', properties: { to: { const: 'full' } }, required: ['to'] }] }
    expect(defaultValueFor(items)).toEqual({ to: 'full' })
  })

  it('object 类型零值不受影响', () => {
    expect(defaultValueFor({ type: 'object' })).toEqual({})
  })
})

describe('FormRenderer 判别数组渲染（docs/119 §2.2/§2.3）', () => {
  it('SSR 渲染不崩：四行分支字段+判别键只读+添加行默认值', () => {
    const html = renderToString(
      createElement(FormRenderer, {
        schema: ruleArraySchema,
        value: [
          { to: 'internal', tenants: ['acme'] },
          { to: 'canary', percent: 5 },
        ],
        source: 'node',
        onChange: () => undefined,
      }),
    )
    expect(html).toContain('internal')
    expect(html).toContain('tenants')
    expect(html).toContain('canary')
  })
})
