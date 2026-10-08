import { describe, expect, it } from 'vitest'
import {
  templateParamsToMetaSchema,
  templateParamsToUiSchema,
} from '../templateParamSchema'
import { buildFormTree, type FormWidgetNode } from '../formTree'
import { applyUiSchema, decorateNodeForRender, type UiSchema } from '../uiSchema'
import type { TemplateParams } from '../../apiClient'

const PARAMS: TemplateParams = {
  min_amount: { type: 'number', label: '最小金额', required: true, hint: '低于此金额不退款' },
  channel: { type: 'select', label: '渠道', options: ['email', 'webhook', 'im'], default: 'webhook' },
  notify: { type: 'boolean', label: '是否通知', required: false },
  note: { type: 'string', label: '备注', required: false },
}

describe('templateParamsToMetaSchema (U1245)', () => {
  it('maps four param types with required/default/enum', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    expect(schema.type).toBe('object')
    expect(schema.required).toEqual(['min_amount'])
    expect(schema.properties?.min_amount).toEqual({ type: 'number' })
    expect(schema.properties?.channel).toEqual({
      type: 'string',
      enum: ['email', 'webhook', 'im'],
      default: 'webhook',
    })
    expect(schema.properties?.notify).toEqual({ type: 'boolean' })
    expect(schema.properties?.note).toEqual({ type: 'string' })
  })

  it('returns empty object schema for undefined/empty params', () => {
    expect(templateParamsToMetaSchema(undefined)).toEqual({ type: 'object', properties: {} })
    expect(templateParamsToMetaSchema({})).toEqual({ type: 'object', properties: {} })
  })

  it('omits required when none declared and degrades select without options', () => {
    const schema = templateParamsToMetaSchema({
      free: { type: 'select', label: '无选项' },
    })
    expect(schema.required).toBeUndefined()
    expect(schema.properties?.free).toEqual({ type: 'string' })
  })
})

describe('templateParamsToUiSchema (U1246)', () => {
  it('carries label/hint into labels/hints slots', () => {
    const ui = templateParamsToUiSchema(PARAMS)
    expect(ui.labels).toMatchObject({ min_amount: '最小金额', channel: '渠道', note: '备注' })
    expect(ui.hints).toEqual({ min_amount: '低于此金额不退款' })
  })

  it('returns empty uiSchema when no label/hint declared', () => {
    expect(templateParamsToUiSchema({ x: { type: 'string' } })).toEqual({})
  })
})

describe('UiSchema hints slot rendering contract (U1247)', () => {
  it('bakes hint into widget node via decorateNodeForRender', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    const tree = buildFormTree(schema, { min_amount: 100 }, { source: 'tool' })
    expect(tree.kind).toBe('group')
    if (tree.kind !== 'group') return
    const minNode = tree.children.find((c) => c.path.at(-1) === 'min_amount')
    expect(minNode?.kind).toBe('widget')
    if (minNode?.kind !== 'widget') return
    const decorated = decorateNodeForRender(minNode, templateParamsToUiSchema(PARAMS))
    expect(decorated.kind).toBe('widget')
    if (decorated.kind !== 'widget') return
    expect(decorated.hint).toBe('低于此金额不退款')
    expect(decorated.label).toBe('最小金额')
  })

  it('leaves hint undefined for fields without hint', () => {
    const ui: UiSchema = { labels: { note: '备注' } }
    const tree = buildFormTree(templateParamsToMetaSchema(PARAMS), { note: 'x' }, { source: 'tool' })
    expect(tree.kind).toBe('group')
    if (tree.kind !== 'group') return
    const noteNode = tree.children.find((c) => c.path.at(-1) === 'note')
    expect(noteNode?.kind).toBe('widget')
    if (noteNode?.kind !== 'widget') return
    const decorated = decorateNodeForRender(noteNode, ui)
    expect(decorated.kind).toBe('widget')
    if (decorated.kind !== 'widget') return
    expect(decorated.hint).toBeUndefined()
  })

  it('applyUiSchema passes through with hints untouched when absent', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    const tree = buildFormTree(schema, { min_amount: 100 }, { source: 'tool' })
    const applied = applyUiSchema(tree, { min_amount: 100 })
    expect(applied).toBe(tree)
  })
})

describe('template param wizard schema-driven contract (U1248)', () => {
  it('derives one widget field per declared param', () => {
    const schema = templateParamsToMetaSchema(PARAMS)
    const tree = buildFormTree(schema, { min_amount: 100, channel: 'webhook', notify: true, note: 'x' }, { source: 'tool' })
    expect(tree.kind).toBe('group')
    if (tree.kind !== 'group') return
    const widgetNames = tree.children
      .filter((c): c is FormWidgetNode => c.kind === 'widget')
      .map((c) => c.widget)
    expect(widgetNames).toContain('number')
    expect(widgetNames).toContain('select')
    expect(widgetNames).toContain('switch')
    // source='tool' 时 string 按 M0 工具表单语义升级为 variable-input（无 scope 退化 TextArea，不崩）。
    expect(widgetNames).toContain('variable-input')
  })
})
