import { describe, expect, it } from 'vitest'
import { humanApprovalSchema } from '../../schemas/nodes/human_approval.schema'
import { parallelSchema } from '../../schemas/nodes/parallel.schema'
import { conditionSchema } from '../../schemas/nodes/condition.schema'
import { subgraphSchema } from '../../schemas/nodes/subgraph.schema'
import { loopSchema } from '../../schemas/nodes/loop.schema'
import { buildNodeRegistry, SAVED_GRAPH_SELECT_WIDGET, TARGET_SELECT_WIDGET } from '../nodeRegistry'
import {
  conditionUiSchema,
  humanApprovalUiSchema,
  loopUiSchema,
  NODE_UI_SCHEMAS,
  parallelUiSchema,
  subgraphUiSchema,
} from '../nodeUiSchemas'
import { BUILTIN_WIDGETS } from '../types'
import { pointerMatches } from '../uiSchema'

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

describe('loop UISchema 与数据 schema 对齐（迁移等价基线）', () => {
  const properties = loopSchema.properties ?? {}
  const propKeys = Object.keys(properties)
  const visibleKeys = propKeys.filter((key) => !(loopUiSchema.hideFields ?? []).includes(key))

  it('每个可见 config 字段都有中文 label，mode 静态隐藏', () => {
    for (const key of visibleKeys) {
      expect(loopUiSchema.labels?.[key], `字段 ${key} 缺中文 label`).toBeTruthy()
    }
    expect(loopUiSchema.hideFields).toEqual(['mode'])
    expect(properties.mode).toBeTruthy()
  })

  it('continueExpression 标 x-variable，双 target 标 x-widget target-select', () => {
    expect(properties.continueExpression['x-variable']).toBe(true)
    expect(properties.bodyTarget['x-widget']).toBe(TARGET_SELECT_WIDGET)
    expect(properties.exitTarget['x-widget']).toBe(TARGET_SELECT_WIDGET)
  })

  it('labels/placeholders/hideFields 引用的字段都在 schema properties 内（无悬空键）', () => {
    const declared = new Set(propKeys)
    for (const key of Object.keys(loopUiSchema.labels ?? {})) {
      expect(declared.has(key), `label 字段 ${key} 未声明`).toBe(true)
    }
    for (const key of Object.keys(loopUiSchema.placeholders ?? {})) {
      expect(declared.has(key), `placeholder 字段 ${key} 未声明`).toBe(true)
    }
    for (const key of loopUiSchema.hideFields ?? []) {
      expect(declared.has(key), `hideField ${key} 未声明`).toBe(true)
    }
  })

  it('已登记到 NODE_UI_SCHEMAS', () => {
    expect(NODE_UI_SCHEMAS.loop).toBe(loopUiSchema)
  })
})

describe('parallel 迁移对齐（M4 批 2 ⑨）', () => {
  const properties = parallelSchema.properties ?? {}
  const branchesItems = (properties.branches.items?.properties ?? {}) as Record<string, { 'x-widget'?: string }>

  it('joinStrategy 补 enum + radio，每个枚举值都有中文长文案', () => {
    expect(properties.joinStrategy.type).toBe('string')
    expect(properties.joinStrategy.enum).toEqual(['all_success', 'all_completed'])
    expect(properties.joinStrategy['x-widget']).toBe('radio')
    for (const value of properties.joinStrategy.enum ?? []) {
      expect(parallelUiSchema.optionLabels?.joinStrategy?.[String(value)]).toBeTruthy()
    }
  })

  it('分支入口与汇聚目标标 x-widget target-select，数组 2-10 门控', () => {
    expect(branchesItems.target['x-widget']).toBe(TARGET_SELECT_WIDGET)
    expect(properties.joinTarget['x-widget']).toBe(TARGET_SELECT_WIDGET)
    expect(properties.branches.minItems).toBe(2)
    expect(properties.branches.maxItems).toBe(10)
  })

  it('数组行占位键能命中 branches[].label/target 指针', () => {
    expect(pointerMatches('/branches/0/label', 'branches[].label')).toBe(true)
    expect(parallelUiSchema.placeholders?.['branches[].label']).toBeTruthy()
    expect(parallelUiSchema.placeholders?.['branches[].target']).toBeTruthy()
    expect(parallelUiSchema.labels?.joinTarget).toContain('汇聚目标')
  })

  it('已登记到 NODE_UI_SCHEMAS', () => {
    expect(NODE_UI_SCHEMAS.parallel).toBe(parallelUiSchema)
  })
})

describe('condition 迁移对齐（M4 批 3 ⑬）', () => {
  const properties = conditionSchema.properties ?? {}
  const branchProps = (properties.branches.items?.properties ?? {}) as Record<
    string,
    { 'x-widget'?: string; 'x-variable'?: boolean }
  >

  it('expression 标 x-variable（variable-input：TextArea+变量插入），target/defaultTarget 标 target-select', () => {
    expect(branchProps.expression['x-variable']).toBe(true)
    expect(branchProps.target['x-widget']).toBe(TARGET_SELECT_WIDGET)
    expect(properties.defaultTarget['x-widget']).toBe(TARGET_SELECT_WIDGET)
  })

  it('branches minItems 1 且无 maxItems（分支数无上限，最后一个禁用删除）', () => {
    expect(properties.branches.minItems).toBe(1)
    expect(properties.branches.maxItems).toBeUndefined()
  })

  it('数组行三件套占位键命中 branches[].*，expression 给 2 行 TextArea，defaultTarget 有中文标题', () => {
    expect(pointerMatches('/branches/0/label', 'branches[].label')).toBe(true)
    expect(pointerMatches('/branches/0/expression', 'branches[].expression')).toBe(true)
    expect(pointerMatches('/branches/0/target', 'branches[].target')).toBe(true)
    expect(conditionUiSchema.placeholders?.['branches[].label']).toContain('分支名')
    expect(conditionUiSchema.placeholders?.['branches[].expression']).toContain('{{')
    expect(conditionUiSchema.placeholders?.['branches[].target']).toContain('目标节点')
    expect(conditionUiSchema.rows?.['branches[].expression']).toBe(2)
    expect(conditionUiSchema.labels?.defaultTarget).toContain('默认分支')
  })

  it('已登记到 NODE_UI_SCHEMAS', () => {
    expect(NODE_UI_SCHEMAS.condition).toBe(conditionUiSchema)
  })
})

describe('subgraph 迁移对齐（M4 批 2 ⑨）', () => {
  const properties = subgraphSchema.properties ?? {}

  it('graphId 标 x-widget saved-graph-select，节点控件表已注册', () => {
    expect(properties.graphId['x-widget']).toBe(SAVED_GRAPH_SELECT_WIDGET)
    const registry = buildNodeRegistry()
    expect(registry.has(SAVED_GRAPH_SELECT_WIDGET)).toBe(true)
  })

  it('inputs 值为 x-variable 模板字符串，UiSchema 给键值行文案与单行 rows', () => {
    const valueSchema = properties.inputs.additionalProperties as { 'x-variable'?: boolean }
    expect(valueSchema?.['x-variable']).toBe(true)
    expect(subgraphUiSchema.labels?.inputs).toContain('子图入参映射')
    expect(subgraphUiSchema.keyPlaceholders?.inputs).toBe('入参键')
    expect(subgraphUiSchema.rows?.['inputs.*']).toBe(1)
  })

  it('已登记到 NODE_UI_SCHEMAS', () => {
    expect(NODE_UI_SCHEMAS.subgraph).toBe(subgraphUiSchema)
  })
})
