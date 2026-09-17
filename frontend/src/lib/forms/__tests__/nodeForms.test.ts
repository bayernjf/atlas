import { describe, expect, it } from 'vitest'
import { humanApprovalSchema } from '../../schemas/nodes/human_approval.schema'
import { buildNodeRegistry, TARGET_SELECT_WIDGET } from '../nodeRegistry'
import { humanApprovalUiSchema, NODE_UI_SCHEMAS } from '../nodeUiSchemas'
import { BUILTIN_WIDGETS } from '../types'

describe('节点控件表 buildNodeRegistry（M4）', () => {
  it('含全部内置控件（九件）+ target-select 业务控件', () => {
    const registry = buildNodeRegistry()
    for (const name of BUILTIN_WIDGETS) {
      expect(registry.has(name)).toBe(true)
      expect(typeof registry.get(name)).toBe('function')
    }
    expect(registry.has(TARGET_SELECT_WIDGET)).toBe(true)
  })
})

describe('human_approval UISchema 与数据 schema 对齐（迁移等价基线）', () => {
  const properties = humanApprovalSchema.properties ?? {}
  const propKeys = Object.keys(properties)

  it('每个 config 字段都有中文 label', () => {
    for (const key of propKeys) {
      expect(humanApprovalUiSchema.labels?.[key], `字段 ${key} 缺中文 label`).toBeTruthy()
    }
  })

  it('双目标进同一 row 视觉组，字段与顺序固定', () => {
    const group = humanApprovalUiSchema.groups?.[0]
    expect(group?.layout).toBe('row')
    expect(group?.fields).toEqual(['approvedTarget', 'rejectedTarget'])
  })

  it('onTimeout 每个枚举值都有中文文案', () => {
    const enums = (properties.onTimeout.enum ?? []) as unknown[]
    expect(enums.length).toBeGreaterThan(0)
    for (const value of enums) {
      expect(
        humanApprovalUiSchema.optionLabels?.onTimeout?.[String(value)],
        `枚举 ${String(value)} 缺中文文案`,
      ).toBeTruthy()
    }
  })

  it('labels/placeholders/groups 引用的字段都在 schema properties 内（无悬空键）', () => {
    const declared = new Set(propKeys)
    for (const key of Object.keys(humanApprovalUiSchema.labels ?? {})) {
      expect(declared.has(key), `label 字段 ${key} 未声明`).toBe(true)
    }
    for (const key of Object.keys(humanApprovalUiSchema.placeholders ?? {})) {
      expect(declared.has(key), `placeholder 字段 ${key} 未声明`).toBe(true)
    }
    for (const group of humanApprovalUiSchema.groups ?? []) {
      for (const field of group.fields) expect(declared.has(field), `分组字段 ${field} 未声明`).toBe(true)
    }
  })

  it('双 target 字段在 schema 上标 x-widget target-select、onTimeout 标 radio', () => {
    expect(properties.approvedTarget['x-widget']).toBe(TARGET_SELECT_WIDGET)
    expect(properties.rejectedTarget['x-widget']).toBe(TARGET_SELECT_WIDGET)
    expect(properties.onTimeout['x-widget']).toBe('radio')
  })

  it('已登记到 NODE_UI_SCHEMAS', () => {
    expect(NODE_UI_SCHEMAS.human_approval).toBe(humanApprovalUiSchema)
  })
})
