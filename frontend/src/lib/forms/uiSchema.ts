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
  /**
   * true＝判别字段与取值位于根 config（如 condition 的 branches[] 行内
   * 字段随根 conditionMode 显隐）；规则作用于每个嵌套 object 组，show
   * 按嵌套组内的局部字段名匹配。
   */
  rootScoped?: boolean
}

export type UiSchema = {
  groups?: UiGroup[]
  hiddenWhen?: UiHiddenWhen[]
  /** 静态隐藏的根层字段名（内部/不暴露字段，如 loop.mode）；恒不渲染，值仍保留。 */
  hideFields?: string[]
  /**
   * 字段中文标题（字段名 → 文案）。MetaSchema 白名单不含 JSON Schema 的 title
   * （04 §4.9 锁定同源 20 keyword、后端零改动），节点表单的设计态中文文案统一由
   * 前端 UISchema 承接；缺省显示字段名。
   * M4 批 2 起键支持嵌套路径通配：根字段 'joinTarget'；数组行 'branches[].label'
   * （[] 匹配任一下标）；键值行值 'inputs.*'（* 匹配任一键）。
   */
  labels?: Record<string, string>
  /** 字段占位提示（键规则同 labels），透传给叶子控件。 */
  placeholders?: Record<string, string>
  /** enum/radio 选项中文文案（键规则同 labels，值为选项值 → 文案）；缺省显示原始枚举值。 */
  optionLabels?: Record<string, Record<string, string>>
  /** 多行控件行数（键规则同 labels），如把 keyvalue 内 variable-input 压成单行。 */
  rows?: Record<string, number>
  /** keyvalue 键输入框占位（键为 keyvalue 字段路径，如 'inputs'）。 */
  keyPlaceholders?: Record<string, string>
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
  const rules = (uiSchema?.hiddenWhen ?? []).filter((rule) => !rule.rootScoped)
  return computeHiddenFields(rules, record)
}

/**
 * 嵌套 object 组的隐藏字段集合：只取 rootScoped 规则，以根 config 为判别
 * record，show 按当前嵌套组内的局部字段名过滤（condition branches[] 行用）。
 */
export function nestedHiddenFields(
  uiSchema: UiSchema | undefined,
  rootRecord: unknown,
): Set<string> {
  const rules = (uiSchema?.hiddenWhen ?? []).filter((rule) => rule.rootScoped)
  return computeHiddenFields(rules, rootRecord)
}

function computeHiddenFields(
  rules: UiHiddenWhen[],
  record: unknown,
): Set<string> {
  if (rules.length === 0) return new Set()
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

  // 一律按字段 key（path 末段）匹配，不依赖 label——label 可能已被 decorateField
  // 烘焙成中文标题，而 group.fields 用的是数据字段名。
  const byField = new Map<string, FormNode>()
  for (const child of children) {
    const key = fieldKey(child)
    if (key) byField.set(key, child)
  }

  const result: FormNode[] = []
  const placed = new Set<string>()
  for (const child of children) {
    const key = fieldKey(child)
    const owner = key ? groups.find((group) => group.fields.includes(key)) : undefined
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
 * 隐藏/分组均按字段 key（path 末段）判定，label 仅作展示，故 decorate 改中文
 * label 不影响分组与显隐。
 */
export function applyUiSchema(tree: FormNode, value: unknown, uiSchema?: UiSchema): FormNode {
  if (!uiSchema || tree.kind !== 'group') return tree
  const hidden = hiddenFields(uiSchema, value)
  const staticHidden = new Set(uiSchema.hideFields ?? [])
  const visibleChildren = tree.children
    .filter((child) => {
      const key = fieldKey(child)
      return !key || (!hidden.has(key) && !staticHidden.has(key))
    })
    .map((child) => decorateField(child, uiSchema))
  const children = applyGroups(visibleChildren, uiSchema.groups, {
    path: tree.path,
    pointer: tree.pointer,
    schema: tree.schema,
  })
  return { ...tree, children }
}

/** 取节点对应的根层字段名（path 末段）；视觉组 path 同父，末段非 string，返回 undefined。 */
function fieldKey(node: FormNode): string | undefined {
  const last = node.path[node.path.length - 1]
  return typeof last === 'string' ? last : undefined
}

/**
 * 把 UISchema 文案键转成 pointer 段序列（M4 批 2 嵌套支持）：
 * 根字段 'joinTarget' → ['joinTarget']；'branches[].label' → ['branches','*','label']；
 * 'inputs.*' → ['inputs','*']。'*' 匹配任意单段（数组下标或键值行键名）。
 * 接受点号或斜杠分隔（'branches[].label' 与斜杠指针形式等价）。
 */
export function uiKeySegments(key: string): string[] {
  const normalized = key.replace(/\[\]/g, '.*').replace(/^\//, '')
  return normalized
    .split(/[./]/)
    .filter(Boolean)
    .map((segment) => (segment === '*' ? '*' : segment))
}

/** 判断节点 RFC6901 pointer 是否命中 UISchema 文案键（通配规则见 uiKeySegments）。 */
export function pointerMatches(pointer: string, key: string): boolean {
  const pattern = uiKeySegments(key)
  const actual = pointer.split('/').filter(Boolean)
  if (pattern.length !== actual.length) return false
  return pattern.every((segment, index) => segment === '*' || segment === actual[index])
}

function lookupByPointer<T>(table: Record<string, T> | undefined, pointer: string): T | undefined {
  if (!table) return undefined
  for (const [key, value] of Object.entries(table)) {
    if (pointerMatches(pointer, key)) return value
  }
  return undefined
}

/**
 * 渲染期逐节点装饰（M4 批 2）：FormRenderer 渲染每个 FormNode（含数组行/键值行等
 * 懒建子树）前调用，按 pointer 通配烘焙 labels/placeholders/optionLabels/rows/
 * keyPlaceholders。根层的 groups/hiddenWhen 仍只在 applyUiSchema 处理一次。
 */
export function decorateNodeForRender(node: FormNode, uiSchema?: UiSchema): FormNode {
  if (!uiSchema) return node
  if (node.kind === 'widget') {
    return {
      ...node,
      label: lookupByPointer(uiSchema.labels, node.pointer) ?? node.label,
      placeholder: lookupByPointer(uiSchema.placeholders, node.pointer) ?? node.placeholder,
      optionLabels: lookupByPointer(uiSchema.optionLabels, node.pointer) ?? node.optionLabels,
      rows: lookupByPointer(uiSchema.rows, node.pointer) ?? node.rows,
    }
  }
  if (node.kind === 'keyvalue') {
    return {
      ...node,
      label: lookupByPointer(uiSchema.labels, node.pointer) ?? node.label,
      keyPlaceholder:
        lookupByPointer(uiSchema.keyPlaceholders, node.pointer) ?? node.keyPlaceholder,
    }
  }
  // group/array 兜底：显式空串（''）表达「不要标题」，须与未命中（undefined）区分——
  // 后者回退 buildFormTree 的字段名直出，前者用于数组/分组顶部标题由外层组件承接的场景
  //（如 condition 瘦包装已有强标题，branches 数组不再重复显示字段名）。
  const label = lookupByPointer(uiSchema.labels, node.pointer)
  return label !== undefined ? { ...node, label } : node
}

/**
 * 把 labels/placeholders/optionLabels 烘焙到根层字段节点（在分组重组前执行，
 * 故平铺字段与将进入视觉组的字段都被覆盖）。只覆盖声明了的键，其余原样。
 */
function decorateField(node: FormNode, uiSchema: UiSchema): FormNode {
  const key = fieldKey(node)
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
