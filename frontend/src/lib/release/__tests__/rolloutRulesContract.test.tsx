/**
 * RolloutModal 规则区迁 forms 契约测试（docs/120 §7，2026-10-10）。
 *
 * 覆盖：buildRolloutRulesSchema 判别协议（三分支、to 键、const 唯一）；normalizeRolloutRules
 * 段唯一＋固定序＋未知段剔除＋bucket field/op 注入；oneOfBranchDefault 目标分支默认
 * 对象；FormRenderer 判别数组渲染（SSR 不崩、行头段切换 Select、行内 const 判别键隐藏、
 * disabled 时 fieldset 禁用、缺省无 fieldset）。
 *
 * 交互级验证（点击添加/切换 Select）走真实浏览器冒烟（项目惯例：vitest 纯函数＋SSR
 * 结构断言，jsdom 交互不引入新依赖）。
 */
import { describe, expect, it } from 'vitest'
import { renderToString } from 'react-dom/server'
import { createElement } from 'react'
import { FormRenderer } from '../../forms/FormRenderer'
import { buildFormTree, oneOfBranchDefault, oneOfDiscriminant, setAtPath } from '../../forms/formTree'
import { buildRolloutRulesSchema, normalizeRolloutRules } from '../rolloutRulesSchema'
import type { RolloutRule } from '../../apiClient'

const schema = buildRolloutRulesSchema()

describe('buildRolloutRulesSchema 判别协议', () => {
  it('items.oneOf 判别成立：key=to、三分支、const 唯一', () => {
    const items = schema.items as { oneOf: unknown[] }
    const d = oneOfDiscriminant(items.oneOf as never)
    expect(d).not.toBeNull()
    expect(d!.key).toBe('to')
    expect([...d!.branches.keys()]).toEqual(['internal', 'lowValueBucket', 'canary'])
    expect(d!.branches.get('internal')!.properties!.tenants.type).toBe('array')
    expect(d!.branches.get('lowValueBucket')!.properties!.value.default).toBe(200)
    expect(d!.branches.get('lowValueBucket')!.properties!.percent.default).toBe(100)
    expect(d!.branches.get('canary')!.properties!.percent.default).toBe(5)
  })

  it('编辑面不含 full 分支（canary 期剔除语义在后端，属运行态）', () => {
    const items = schema.items as { oneOf: unknown[] }
    const d = oneOfDiscriminant(items.oneOf as never)!
    expect(d.branches.has('full')).toBe(false)
  })
})

describe('normalizeRolloutRules 段唯一＋固定序', () => {
  const r = (to: RolloutRule['to'], extra: object = {}): RolloutRule =>
    ({ to, ...extra }) as RolloutRule

  it('乱序输入收敛为 ROLLOUT_RULE_ORDER 固定序', () => {
    const next = normalizeRolloutRules([
      r('canary', { percent: 5 }),
      r('lowValueBucket', { value: 200, percent: 100 }),
      r('internal', { tenants: ['t1'] }),
    ])
    expect(next.map((x) => x.to)).toEqual(['internal', 'lowValueBucket', 'canary'])
  })

  it('同段重复只保留最后一条', () => {
    const next = normalizeRolloutRules([
      r('internal', { tenants: ['t1'] }),
      r('internal', { tenants: ['t2'] }),
      r('canary', { percent: 5 }),
    ])
    expect(next.filter((x) => x.to === 'internal')).toHaveLength(1)
    expect(next.find((x) => x.to === 'internal')!.tenants).toEqual(['t2'])
  })

  it('未知段剔除、空数组保留空、full 段保留（兼容既有配置）', () => {
    expect(normalizeRolloutRules([{ to: 'unknown' } as unknown as RolloutRule])).toEqual([])
    expect(normalizeRolloutRules([])).toEqual([])
    expect(normalizeRolloutRules([r('full')])).toEqual([{ to: 'full' }])
  })

  it('bucket 行注入固定形状 field/op；已含则保留', () => {
    const injected = normalizeRolloutRules([r('lowValueBucket', { value: 300, percent: 50 })])[0]
    expect(injected).toMatchObject({ field: 'payload.amount', op: '<=', value: 300, percent: 50 })
    const kept = normalizeRolloutRules([
      r('lowValueBucket', { field: 'payload.total', op: '<=', value: 1, percent: 2 }),
    ])[0]
    expect((kept as { field?: string }).field).toBe('payload.total')
  })
})

describe('oneOfBranchDefault 目标分支默认对象', () => {
  const items = schema.items as { oneOf: unknown[] }
  const d = oneOfDiscriminant(items.oneOf as never)!

  it('internal 分支：判别键=const＋tenants 空数组', () => {
    expect(oneOfBranchDefault(d, 'internal')).toEqual({ to: 'internal', tenants: [] })
  })

  it('bucket 分支：value/percent 取 schema 默认（200/100）', () => {
    expect(oneOfBranchDefault(d, 'lowValueBucket')).toEqual({
      to: 'lowValueBucket',
      value: 200,
      percent: 100,
    })
  })

  it('canary 分支：percent 默认 5', () => {
    expect(oneOfBranchDefault(d, 'canary')).toEqual({ to: 'canary', percent: 5 })
  })

  it('未登记 const 值 → null（调用方忽略，行值不动）', () => {
    expect(oneOfBranchDefault(d, 'full')).toBeNull()
  })

  it('切换语义＝行值替换为目标分支默认对象（setAtPath 组合）', () => {
    const root = [{ to: 'internal', tenants: ['t1'] }]
    const nextRow = oneOfBranchDefault(d, 'lowValueBucket')!
    const next = setAtPath(root, [0], nextRow) as RolloutRule[]
    expect(next[0]).toEqual({ to: 'lowValueBucket', value: 200, percent: 100 })
  })
})

describe('FormRenderer 判别数组渲染（SSR 结构断言）', () => {
  const render = (value: unknown, disabled = false) =>
    renderToString(
      createElement(FormRenderer, {
        schema,
        value,
        source: 'node',
        disabled,
        onChange: () => undefined,
      }),
    )

  it('空数组渲染不崩，无行', () => {
    const html = render([])
    expect(html).toContain('添 加')
    expect(html.match(/ant-select-single/g)).toBeNull()
  })

  it('canary 行：行头段切换 Select 渲染且仅一处（行内 const 判别键隐藏）', () => {
    const html = render([{ to: 'canary', percent: 5 }])
    // antd Select 根元素独有 ant-select-single token；行内判别键字段被过滤后不产生第二个 Select。
    expect(html.match(/ant-select-single/g)).toHaveLength(1)
    expect(html).toContain('canary')
    expect(html).toContain('percent')
  })

  it('bucket 行：value/percent 字段渲染（SSR 数字输入）', () => {
    const html = render([{ to: 'lowValueBucket', value: 200, percent: 100 }])
    expect(html.match(/ant-select-single/g)).toHaveLength(1)
    expect(html).toContain('200')
  })

  it('disabled=true 时输出 fieldset disabled；缺省 false 无 fieldset', () => {
    expect(render([], true)).toContain('<fieldset disabled=""')
    expect(render([])).not.toContain('fieldset')
  })

  it('formTree 判别数组：canary 行 value 分支匹配、行 path 可寻址', () => {
    const tree = buildFormTree(schema, [{ to: 'canary', percent: 5 }], { source: 'node' })
    if (tree.kind !== 'array') throw new Error('expected array node')
    expect(tree.items[0].kind).toBe('group')
    expect(tree.items[0].path).toEqual([0])
  })
})
