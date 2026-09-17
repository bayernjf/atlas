/**
 * UISchema 最小子集（M4，04 §4.10 扩展 / 03 `ui_schema` / ADR T17 复查不重开）。
 *
 * 立项前复杂度 dump 实测九节点 schema 后，真实 UI 布局需求只有两件：
 * - groups（ui:group）：把一个 object 的若干字段包进**视觉分组**，`layout:'row'`
 *   让组内字段并排（HumanApproval 的 approved/rejected 双目标）。纯视觉，不增加
 *   数据层级——组内字段的 path/pointer 仍相对对象根，更新语义不变。
 * - hiddenWhen（条件显隐）：按判别字段当前值显隐受控字段（trigger 的
 *   cron/webhookUrl 随 triggerType 显隐）；被隐藏字段不渲染。
 *
 * 落码 HumanApproval 时补字段文案（M3 式落码细化，仍自研最小层、零依赖/零后端）：
 * MetaSchema 白名单无 title，节点表单的中文标题/占位/枚举文案无处安放，统一由
 * labels/placeholders/optionLabels 承接（只作用根层字段）。
 *
 * 明确不做（08 M4 立项非目标）：完整 UISchema 规范、ui:order、if/then 动态依赖。
 * 本模块纯逻辑、零 React，可在 node 环境单测；FormRenderer 只做薄接入。
 */
import type { MetaSchema } from '../schemas/metaSchema'
import { isPlainObject, type FormGroupNode, type FormNode, type FormPath } from './formTree'

export type UiGroupLayout = 'row' | 'column'

export type UiGroup = {
  /** 分组唯一键（React key / 去重用）。 */
  key: string
  /** 分组标题；空串则不渲染标题（仅作布局容器）。 */
  label?: string
  /** 归入本组的字段名（相对当前 object），按此顺序排列。 */
  fields: string[]
  /** 缺省 'column'（垂直堆叠）；'row' 组内字段并排。 */
  layout?: UiGroupLayout
}

export type UiHiddenWhen = {
  /** 判别字段名（相对当前 object）。 */
  field: string
  /** 判别值：当前 record[field] === equals 时本条命中。 */
  equals: unknown
  /** 本条命中时显示的受控字段；同一受控字段在未命中的分支下隐藏。 */
  show: string[]
}

export type UiSchema = {
  groups?: UiGroup[]
  hiddenWhen?: UiHiddenWhen[]
  /**
   * 字段中文标题（字段名 → 文案）。MetaSchema 白名单不含 JSON Schema 的 title
   * （04 §4.9 锁定同源 20 keyword、后端零改动），节点表单的设计态中文文案统一由
   * 前端 UISchema 承接；缺省显示字段名。
   */
  labels?: Record<string, string>
  /** 字段占位提示（字段名 → 文案），透传给叶子控件。 */
  placeholders?: Record<string, string>
  /** enum/radio 选项中文文案（字段名 → 选项值 → 文案）；缺省显示原始枚举值。 */
  optionLabels?: Record<string, Record<string, string>>
}

type GroupEnvelope = {
  /** 视觉组沿用父 object 的 path/pointer/schema（不增加数据层级）。 */
  path: FormPath
  pointer: string
  schema: MetaSchema
}

/**
 * 计算当前值下应隐藏的受控字段集合（CondResolver 核心）。
 *
 * 语义：出现在任一规则 show 中的字段为「受控字段」；只有当某条规则的判别值
 * 命中当前 record 时，其 show 字段才可见，其余受控字段一律隐藏。由此天然支持
 * 「manual 分支无 show → cron/webhookUrl 全隐」这类没有规则命中的情形。
 */
export function hiddenFields(uiSchema: UiSchema | undefined, record: unknown): Set<string> {
  const rules = uiSchema?.hiddenWhen
  if (!rules || rules.length === 0) return new Set()
  const current = isPlainObject(record) ? record : {}

  const controlled = new Set<string>()
  for (const rule of rules) for (const field of rule.show) controlled.add(field)

  const visible = new Set<string>()
  for (const rule of rules) {
    if (current[rule.field] === rule.equals) {
      for (const field of rule.show) visible.add(field)
    }
  }

  return new Set([...controlled].filter((field) => !visible.has(field)))
}

/**
 * 把 object 的平铺 children 按 groups 重组为视觉分组（ui:group）。
 *
 * 未归入任何组的字段按原 properties 顺序平铺；分组在其「组内最早出现字段」的
 * 位置整体插入，组内字段按 group.fields 顺序。被前置 hiddenWhen 过滤掉、或未在
 * schema 声明的字段不出现；整组字段都缺失时不渲染空组。纯视觉组 path/pointer
 * 同父对象（visual:true），不参与数据寻址。
 */
export function applyGroups(
  children: FormNode[],
  groups: UiGroup[] | undefined,
  envelope: GroupEnvelope,
): FormNode[] {
  if (!groups || groups.length === 0) return children

  const byField = new Map<string, FormNode>()
  for (const child of children) {
    if (child.label) byField.set(child.label, child)
  }

  const result: FormNode[] = []
  const placed = new Set<string>()
  for (const child of children) {
    const owner = groups.find((group) => group.fields.includes(child.label))
    if (!owner) {
      result.push(child)
      continue
    }
    if (placed.has(owner.key)) continue
    placed.add(owner.key)

    const groupChildren = owner.fields
      .map((field) => byField.get(field))
      .filter((node): node is FormNode => node !== undefined)
    if (groupChildren.length === 0) continue

    const visualGroup: FormGroupNode = {
      kind: 'group',
      path: envelope.path,
      pointer: envelope.pointer,
      schema: envelope.schema,
      label: owner.label ?? '',
      required: false,
      children: groupChildren,
      layout: owner.layout ?? 'column',
      visual: true,
    }
    result.push(visualGroup)
  }
  return result
}

/**
 * 把 UISchema 应用到已构建的表单树（FormRenderer 薄接入入口）。
 *
 * 仅处理根 object（M4 最小子集：UISchema 只描述节点 config 根这一层）：先按
 * hiddenWhen 过滤隐藏字段，再烘焙字段文案（labels/placeholders/optionLabels），
 * 最后按 groups 重组视觉分组。非 group 根或无 uiSchema 时原样返回。
 */
export function applyUiSchema(tree: FormNode, value: unknown, uiSchema?: UiSchema): FormNode {
  if (!uiSchema || tree.kind !== 'group') return tree
  const hidden = hiddenFields(uiSchema, value)
  const visibleChildren = tree.children
    .filter((child) => !hidden.has(child.label))
    .map((child) => decorateField(child, uiSchema))
  const children = applyGroups(visibleChildren, uiSchema.groups, {
    path: tree.path,
    pointer: tree.pointer,
    schema: tree.schema,
  })
  return { ...tree, children }
}

/** 取节点对应的根层字段名（path 末段）；视觉组 path 同父，末段非 string，返回 undefined。 */
function rootFieldKey(node: FormNode): string | undefined {
  const last = node.path[node.path.length - 1]
  return typeof last === 'string' ? last : undefined
}

/**
 * 把 labels/placeholders/optionLabels 烘焙到根层字段节点（在分组重组前执行，
 * 故平铺字段与将进入视觉组的字段都被覆盖）。只覆盖声明了的键，其余原样。
 */
function decorateField(node: FormNode, uiSchema: UiSchema): FormNode {
  const key = rootFieldKey(node)
  if (!key) return node
  const label = uiSchema.labels?.[key]
  if (node.kind === 'widget') {
    return {
      ...node,
      label: label ?? node.label,
      placeholder: uiSchema.placeholders?.[key] ?? node.placeholder,
      optionLabels: uiSchema.optionLabels?.[key] ?? node.optionLabels,
    }
  }
  return label ? { ...node, label } : node
}
