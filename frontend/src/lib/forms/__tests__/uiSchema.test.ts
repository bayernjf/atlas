import { describe, expect, it } from 'vitest'
import { buildFormTree, type FormGroupNode, type FormNode } from '../formTree'
import { applyGroups, applyUiSchema, hiddenFields, type UiSchema } from '../uiSchema'
import type { MetaSchema } from '../../schemas/metaSchema'

/** 取 group 根的直接子项标签序列（视觉组以 `[a,b]` 表示）。 */
function childOutline(node: FormNode): Array<string | string[]> {
  if (node.kind !== 'group') return []
  return node.children.map((child) =>
    child.kind === 'group' && child.visual === true
      ? child.children.map((grand) => grand.label)
      : child.label,
  )
}

function findVisualGroup(node: FormNode, keyIndex = 0): FormGroupNode {
  const groups = node.kind === 'group' ? node.children.filter((c) => c.kind === 'group' && c.visual) : []
  const found = groups[keyIndex]
  if (!found || found.kind !== 'group') throw new Error('未找到视觉组')
  return found
}

// ---- hiddenFields（CondResolver）------------------------------------------------

const triggerUi: UiSchema = {
  hiddenWhen: [
    { field: 'triggerType', equals: 'schedule', show: ['cron'] },
    { field: 'triggerType', equals: 'webhook', show: ['webhookUrl'] },
  ],
}

describe('hiddenFields', () => {
  it('无 uiSchema / 无 hiddenWhen 时返回空集', () => {
    expect(hiddenFields(undefined, { triggerType: 'manual' }).size).toBe(0)
    expect(hiddenFields({}, { triggerType: 'manual' }).size).toBe(0)
  })

  it('schedule 命中：显 cron、隐 webhookUrl', () => {
    const hidden = hiddenFields(triggerUi, { triggerType: 'schedule' })
    expect(hidden.has('cron')).toBe(false)
    expect(hidden.has('webhookUrl')).toBe(true)
  })

  it('webhook 命中：显 webhookUrl、隐 cron', () => {
    const hidden = hiddenFields(triggerUi, { triggerType: 'webhook' })
    expect(hidden.has('webhookUrl')).toBe(false)
    expect(hidden.has('cron')).toBe(true)
  })

  it('manual 无规则命中：受控字段全隐', () => {
    const hidden = hiddenFields(triggerUi, { triggerType: 'manual' })
    expect([...hidden].sort()).toEqual(['cron', 'webhookUrl'])
  })

  it('判别字段缺失或 record 非对象时，受控字段全隐（fail-safe 收敛）', () => {
    expect([...hiddenFields(triggerUi, {})].sort()).toEqual(['cron', 'webhookUrl'])
    expect([...hiddenFields(triggerUi, null)].sort()).toEqual(['cron', 'webhookUrl'])
  })

  it('equals 做严格相等（数字/布尔不被宽松匹配）', () => {
    const ui: UiSchema = { hiddenWhen: [{ field: 'n', equals: 1, show: ['a'] }] }
    expect(hiddenFields(ui, { n: '1' }).has('a')).toBe(true)
    expect(hiddenFields(ui, { n: 1 }).has('a')).toBe(false)
  })
})

// ---- applyGroups（ui:group）-----------------------------------------------------

const approvalSchema: MetaSchema = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    approver: { type: 'string' },
    timeoutSeconds: { type: 'integer', minimum: 10, maximum: 3600, default: 300 },
    onTimeout: { type: 'string', enum: ['approve', 'reject'], default: 'reject' },
    approvedTarget: { type: 'string' },
    rejectedTarget: { type: 'string' },
  },
  required: ['summary', 'timeoutSeconds', 'approvedTarget', 'rejectedTarget'],
}

const approvalUi: UiSchema = {
  groups: [{ key: 'targets', fields: ['approvedTarget', 'rejectedTarget'], layout: 'row' }],
}

describe('applyGroups', () => {
  it('无 groups 时原样返回 children', () => {
    const tree = buildFormTree(approvalSchema, {}, { source: 'node' })
    const original = tree.kind === 'group' ? tree.children : []
    expect(applyGroups(original, undefined, { path: [], pointer: '', schema: approvalSchema })).toBe(original)
  })

  it('把声明字段包成 visual 组，未分组字段保持平铺，组落在其最早字段位置', () => {
    const tree = applyUiSchema(buildFormTree(approvalSchema, {}, { source: 'node' }), {}, approvalUi)
    expect(childOutline(tree)).toEqual([
      'summary',
      'approver',
      'timeoutSeconds',
      'onTimeout',
      ['approvedTarget', 'rejectedTarget'],
    ])
  })

  it('视觉组不增加数据层级：path/pointer 同父、layout row、visual 标记', () => {
    const tree = applyUiSchema(buildFormTree(approvalSchema, {}, { source: 'node' }), {}, approvalUi)
    const group = findVisualGroup(tree)
    expect(group.visual).toBe(true)
    expect(group.layout).toBe('row')
    expect(group.path).toEqual([])
    expect(group.pointer).toBe('')
    // 组内字段的寻址路径仍是「对象根 → 字段」，不含组这一层
    const [approved, rejected] = group.children
    expect(approved.path).toEqual(['approvedTarget'])
    expect(rejected.path).toEqual(['rejectedTarget'])
    expect(approved.pointer).toBe('/approvedTarget')
  })

  it('组内字段顺序按 group.fields，而非 properties 顺序', () => {
    const ui: UiSchema = {
      groups: [{ key: 'rev', fields: ['rejectedTarget', 'approvedTarget'] }],
    }
    const tree = applyUiSchema(buildFormTree(approvalSchema, {}, { source: 'node' }), {}, ui)
    expect(findVisualGroup(tree).children.map((c) => c.label)).toEqual([
      'rejectedTarget',
      'approvedTarget',
    ])
  })

  it('整组字段都缺失（被隐藏/未声明）时不渲染空组', () => {
    const ui: UiSchema = { groups: [{ key: 'ghost', fields: ['nopeA', 'nopeB'] }] }
    const tree = applyUiSchema(buildFormTree(approvalSchema, {}, { source: 'node' }), {}, ui)
    expect(childOutline(tree)).toEqual([
      'summary',
      'approver',
      'timeoutSeconds',
      'onTimeout',
      'approvedTarget',
      'rejectedTarget',
    ])
  })
})

// ---- applyUiSchema 端到端（hiddenWhen + groups 协同）----------------------------

const triggerSchema: MetaSchema = {
  type: 'object',
  properties: {
    triggerType: { type: 'string', enum: ['manual', 'schedule', 'webhook'], default: 'manual' },
    cron: { type: 'string' },
    webhookUrl: { type: 'string' },
  },
  required: ['triggerType'],
}

describe('applyUiSchema', () => {
  it('非 group 根或无 uiSchema 时原样返回（同引用）', () => {
    const leaf = buildFormTree({ type: 'string' }, 'x', { source: 'node' })
    expect(applyUiSchema(leaf, 'x', triggerUi)).toBe(leaf)
    const group = buildFormTree(triggerSchema, {}, { source: 'node' })
    expect(applyUiSchema(group, {})).toBe(group)
  })

  it.each([
    ['manual', ['triggerType']],
    ['schedule', ['triggerType', 'cron']],
    ['webhook', ['triggerType', 'webhookUrl']],
  ])('trigger triggerType=%s 时只渲染该分支字段', (triggerType, expected) => {
    const tree = applyUiSchema(
      buildFormTree(triggerSchema, { triggerType }, { source: 'node' }),
      { triggerType },
      triggerUi,
    )
    expect(childOutline(tree)).toEqual(expected)
  })

  it('hiddenWhen 与 groups 同时作用：先过滤再分组，隐藏字段不进视觉组', () => {
    const schema: MetaSchema = {
      type: 'object',
      properties: {
        kind: { type: 'string', enum: ['a', 'b'], default: 'a' },
        a1: { type: 'string' },
        a2: { type: 'string' },
        b1: { type: 'string' },
      },
    }
    const ui: UiSchema = {
      groups: [{ key: 'pair', fields: ['a1', 'a2'], layout: 'row' }],
      hiddenWhen: [
        { field: 'kind', equals: 'a', show: ['a1', 'a2'] },
        { field: 'kind', equals: 'b', show: ['b1'] },
      ],
    }
    const onA = applyUiSchema(buildFormTree(schema, { kind: 'a' }, { source: 'node' }), { kind: 'a' }, ui)
    expect(childOutline(onA)).toEqual(['kind', ['a1', 'a2']])
    const onB = applyUiSchema(buildFormTree(schema, { kind: 'b' }, { source: 'node' }), { kind: 'b' }, ui)
    expect(childOutline(onB)).toEqual(['kind', 'b1'])
  })
})
