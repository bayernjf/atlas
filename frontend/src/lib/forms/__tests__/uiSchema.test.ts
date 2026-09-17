import { describe, expect, it } from 'vitest'
import { buildFormTree, type FormGroupNode, type FormNode } from '../formTree'
import {
  applyGroups,
  applyUiSchema,
  decorateNodeForRender,
  hiddenFields,
  pointerMatches,
  type UiSchema,
} from '../uiSchema'
import type { MetaSchema } from '../../schemas/metaSchema'
import { parallelSchema } from '../../schemas/nodes/parallel.schema'
import { subgraphSchema } from '../../schemas/nodes/subgraph.schema'
import { parallelUiSchema, subgraphUiSchema } from '../nodeUiSchemas'

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

  it('回归：labels 烘焙成中文后，groups 仍按字段 key 成组且组内带中文 label', () => {
    const ui: UiSchema = {
      labels: { approvedTarget: '通过目标', rejectedTarget: '拒绝目标' },
      groups: [{ key: 'targets', fields: ['approvedTarget', 'rejectedTarget'], layout: 'row' }],
    }
    const tree = applyUiSchema(buildFormTree(approvalSchema, {}, { source: 'node' }), {}, ui)
    const group = findVisualGroup(tree)
    expect(group.layout).toBe('row')
    expect(group.children.map((c) => c.label)).toEqual(['通过目标', '拒绝目标'])
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

  it('hideFields 静态隐藏内部字段（loop.mode），其余字段照常渲染', () => {
    const schema: MetaSchema = {
      type: 'object',
      properties: {
        mode: { type: 'string' },
        continueExpression: { type: 'string' },
        maxIterations: { type: 'integer' },
      },
    }
    const ui: UiSchema = { hideFields: ['mode'] }
    const tree = applyUiSchema(
      buildFormTree(schema, { mode: 'while', continueExpression: 'x', maxIterations: 10 }, { source: 'node' }),
      { mode: 'while' },
      ui,
    )
    expect(childOutline(tree)).toEqual(['continueExpression', 'maxIterations'])
  })
})

describe('嵌套路径通配装饰（M4 批 2 ⑨：parallel branches / subgraph inputs）', () => {
  it('pointerMatches：[] 匹配任意下标、* 匹配任意单段、根字段无前缀', () => {
    expect(pointerMatches('/branches/0/label', 'branches[].label')).toBe(true)
    expect(pointerMatches('/branches/10/target', 'branches[].target')).toBe(true)
    expect(pointerMatches('/branches/0/label', 'branches[].target')).toBe(false)
    expect(pointerMatches('/joinTarget', 'joinTarget')).toBe(true)
    expect(pointerMatches('/inputs/order_id', 'inputs.*')).toBe(true)
    expect(pointerMatches('/inputs', 'inputs.*')).toBe(false)
    expect(pointerMatches('/inputs/a/b', 'inputs.*')).toBe(false)
  })

  it('parallel：数组行内 label/target 占位与 joinTarget 长 label 按指针烘焙', () => {
    const tree = buildFormTree(
      parallelSchema,
      {
        joinStrategy: 'all_success',
        branches: [
          { label: 'a', target: 'tool-1' },
          { label: '', target: '' },
        ],
        joinTarget: '',
      },
      { source: 'node' },
    )
    const branches = tree.kind === 'group' ? tree.children.find((c) => c.label === 'branches') : undefined
    expect(branches?.kind).toBe('array')
    if (branches?.kind !== 'array') throw new Error('branches 非 array')

    const item0 = branches.items[0]
    if (item0.kind !== 'group') throw new Error('item 非 group')
    const label0 = decorateNodeForRender(item0.children[0], parallelUiSchema)
    const target0 = decorateNodeForRender(item0.children[1], parallelUiSchema)
    expect(label0.kind).toBe('widget')
    expect(target0.kind).toBe('widget')
    if (label0.kind === 'widget') expect(label0.placeholder).toBe('分支名，如：通知商家')
    if (target0.kind === 'widget') expect(target0.placeholder).toBe('分支入口节点（需先在画布连线）')

    const joinTarget = tree.kind === 'group' ? tree.children.find((c) => c.label === 'joinTarget') : undefined
    const decoratedJoin = decorateNodeForRender(joinTarget!, parallelUiSchema)
    expect(decoratedJoin.kind).toBe('widget')
    if (decoratedJoin.kind === 'widget') {
      expect(decoratedJoin.label).toContain('各分支末端都连线到该节点')
      expect(decoratedJoin.placeholder).toBe('选择汇聚目标节点')
    }

    const strategy = tree.kind === 'group' ? tree.children.find((c) => c.label === 'joinStrategy') : undefined
    const decoratedStrategy = decorateNodeForRender(strategy!, parallelUiSchema)
    if (decoratedStrategy.kind === 'widget') {
      expect(decoratedStrategy.optionLabels?.all_success).toContain('全部成功')
      expect(decoratedStrategy.optionLabels?.all_completed).toContain('全部完成')
    }
  })

  it('subgraph：inputs 键值行值节点压单行并给占位，键占位烘焙到 keyvalue 节点', () => {
    const tree = buildFormTree(
      subgraphSchema,
      { graphId: 'g1', inputs: { order_id: '{{trigger-1.x}}' } },
      { source: 'node' },
    )
    if (tree.kind !== 'group') throw new Error('根非 group')
    const inputs = tree.children.find((c) => c.label === 'inputs')
    expect(inputs?.kind).toBe('keyvalue')
    if (inputs?.kind !== 'keyvalue') throw new Error('inputs 非 keyvalue')
    const decoratedKv = decorateNodeForRender(inputs, subgraphUiSchema)
    if (decoratedKv.kind !== 'keyvalue') throw new Error('装饰后非 keyvalue')
    expect(decoratedKv.keyPlaceholder).toBe('入参键')
    expect(decoratedKv.label).toContain('子图入参映射')

    // 键值行值节点在 FormRenderer 内懒建，按同一装饰路径验证
    const valueNode = buildFormTree(inputs.valueSchema, '{{trigger-1.x}}', {
      path: ['inputs', 'order_id'],
      source: 'node',
    })
    const decoratedValue = decorateNodeForRender(valueNode, subgraphUiSchema)
    expect(decoratedValue.kind).toBe('widget')
    if (decoratedValue.kind === 'widget') {
      expect(decoratedValue.rows).toBe(1)
      expect(decoratedValue.placeholder).toBe('{{trigger-1.context.payload.order_id}}')
    }
  })
})
