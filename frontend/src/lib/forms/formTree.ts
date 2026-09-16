/**
 * M3 表单树与不可变更新核心（08 M3 立项条② / 04 §4.10）。
 *
 * 纯逻辑、零 React：schema + 已解析值 → FormNode 树。object 按 properties 顺序
 * 递归分组、array 走 items 增删行、additionalProperties-only object 走键值行，
 * 其余字段按 resolveWidget 的选择/降级序落控件（FormRenderer 只负责薄封装）。
 * 更新一律经路径浅拷贝产出新值：不改入参、不缓存、未渲染的键原样保留
 * （onChange 抛出的永远是「下一整个 params 对象」）。
 */
import type { MetaSchema } from '../schemas/metaSchema'
import { escapePointerToken, type Diagnostic } from '../validation/diagnostics'
import { resolveWidget, type SchemaSource } from './resolveWidget'
import type { WidgetName } from './types'

/** 路径段：对象键为 string、数组下标为 number（pointer 中的数字化形式）。 */
export type FormPathSegment = string | number

export type FormPath = FormPathSegment[]

type FormNodeBase = {
  /** 相对表单根的更新路径（setAtPath/removeAtPath 的入参）。 */
  path: FormPath
  /** RFC 6901 pointer（根为 ''），M2 Diagnostic 按此精确匹配字段。 */
  pointer: string
  /** 当前字段 schema 片段。 */
  schema: MetaSchema
  /** 字段名（对象键 / 数组行号 `#n` / 键值行与根为空串）。 */
  label: string
  required: boolean
}

export type FormWidgetNode = FormNodeBase & {
  kind: 'widget'
  widget: WidgetName
  value: unknown
  description?: string
}

export type FormGroupNode = FormNodeBase & {
  kind: 'group'
  children: FormNode[]
}

export type FormArrayNode = FormNodeBase & {
  kind: 'array'
  /** 逐行渲染的 item 子树（items schema + 当前元素值）。 */
  items: FormNode[]
}

export type FormKeyValueNode = FormNodeBase & {
  kind: 'keyvalue'
  /** 值侧 schema（additionalProperties 子 schema；缺省为空 schema → 降级 json）。 */
  valueSchema: MetaSchema
  entries: Array<{ key: string; value: unknown }>
}

export type FormNode = FormWidgetNode | FormGroupNode | FormArrayNode | FormKeyValueNode

/** 递归上限：超出即降级 json 控件，防病态 schema 无限展开。 */
export const MAX_FORM_DEPTH = 8

export type BuildFormTreeOptions = {
  path?: FormPath
  label?: string
  required?: boolean
  /** 默认 'tool'（M3 唯一消费者是工具 params；节点 schema 走 'node'，即 x-widget 生效）。 */
  source?: SchemaSource
  depth?: number
}

export function buildFormTree(
  schema: MetaSchema,
  value: unknown,
  options: BuildFormTreeOptions = {},
): FormNode {
  const path = options.path ?? []
  const base: FormNodeBase = {
    path,
    pointer: pathToPointer(path),
    schema,
    label: options.label ?? '',
    required: options.required ?? false,
  }
  const source = options.source ?? 'tool'
  const depth = options.depth ?? 0
  const resolution =
    depth >= MAX_FORM_DEPTH
      ? ({ kind: 'widget', widget: 'json' } as const)
      : resolveWidget(schema, source)

  if (resolution.kind === 'group') {
    const record = isPlainObject(value) ? value : {}
    const requiredKeys = new Set(schema.required ?? [])
    const children = Object.entries(schema.properties ?? {}).map(([key, sub]) =>
      buildFormTree(sub, record[key], {
        path: [...path, key],
        label: key,
        required: requiredKeys.has(key),
        source,
        depth: depth + 1,
      }),
    )
    return { ...base, kind: 'group', children }
  }

  if (resolution.kind === 'array') {
    const list = Array.isArray(value) ? value : []
    const itemSchema = schema.items ?? {}
    return {
      ...base,
      kind: 'array',
      items: list.map((item, index) =>
        buildFormTree(itemSchema, item, {
          path: [...path, index],
          label: `#${index + 1}`,
          source,
          depth: depth + 1,
        }),
      ),
    }
  }

  if (resolution.kind === 'keyvalue') {
    const record = isPlainObject(value) ? value : {}
    return {
      ...base,
      kind: 'keyvalue',
      valueSchema: isPlainObject(schema.additionalProperties) ? schema.additionalProperties : {},
      entries: Object.entries(record).map(([key, entryValue]) => ({ key, value: entryValue })),
    }
  }

  return {
    ...base,
    kind: 'widget',
    widget: resolution.widget,
    value,
    description: schema.description,
  }
}

/** 路径 → RFC 6901 pointer（`~` → `~0`、`/` → `~1`；数字段不加引号）。 */
export function pathToPointer(path: FormPath): string {
  let pointer = ''
  for (const segment of path) {
    pointer += typeof segment === 'number' ? `/${segment}` : `/${escapePointerToken(segment)}`
  }
  return pointer
}

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function getAtPath(root: unknown, path: FormPath): unknown {
  let current = root
  for (const segment of path) {
    if (Array.isArray(current)) current = current[Number(segment)]
    else if (isPlainObject(current)) current = current[String(segment)]
    else return undefined
  }
  return current
}

function cloneContainer(value: unknown, segment: FormPathSegment): Record<string, unknown> | unknown[] {
  if (typeof segment === 'number') return Array.isArray(value) ? [...value] : []
  return isPlainObject(value) ? { ...value } : {}
}

/** 不可变写回：沿路径浅拷贝，返回新根（原根与中间层引用不变）。 */
export function setAtPath(root: unknown, path: FormPath, next: unknown): unknown {
  if (path.length === 0) return next
  const [head, ...rest] = path
  const container = cloneContainer(root, head)
  if (Array.isArray(container)) {
    const index = Number(head)
    container[index] = rest.length === 0 ? next : setAtPath(container[index], rest, next)
  } else {
    const key = String(head)
    container[key] = rest.length === 0 ? next : setAtPath(container[key], rest, next)
  }
  return container
}

/** 不可变删除：数组下标走 splice，对象键走 delete。 */
export function removeAtPath(root: unknown, path: FormPath): unknown {
  if (path.length === 0) return root
  const [head, ...rest] = path
  const container = cloneContainer(root, head)
  if (Array.isArray(container)) {
    const index = Number(head)
    if (rest.length === 0) container.splice(index, 1)
    else container[index] = removeAtPath(container[index], rest)
  } else {
    const key = String(head)
    if (rest.length === 0) delete container[key]
    else container[key] = removeAtPath(container[key], rest)
  }
  return container
}

/** 不可变追加：目标非数组时按空数组起步（array 增删行的「添加」入口）。 */
export function appendAtPath(root: unknown, path: FormPath, next: unknown): unknown {
  const current = getAtPath(root, path)
  const list = Array.isArray(current) ? current : []
  return setAtPath(root, path, [...list, next])
}

/**
 * 不可变改名（键值行）：保持既有键顺序，被改名键就地替换。
 * `from` 不存在、`to` 为空、`to` 与既有键冲突时原样返回（同一引用，调用方据此跳过写回）。
 */
export function renameKeyAtPath(root: unknown, path: FormPath, from: string, to: string): unknown {
  const target = getAtPath(root, path)
  if (!isPlainObject(target)) return root
  if (to === from || to === '' || !(from in target) || to in target) return root
  const renamed: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(target)) {
    if (key === from) renamed[to] = value
    else renamed[key] = value
  }
  return setAtPath(root, path, renamed)
}

/** 新增字段/新行的初始值：default → const → enum 首项 → 类型零值。 */
export function defaultValueFor(schema: MetaSchema): unknown {
  if (schema.default !== undefined) return schema.default
  if (schema.const !== undefined) return schema.const
  if (Array.isArray(schema.enum) && schema.enum.length > 0) return schema.enum[0]
  switch (schema.type) {
    case 'string':
      return ''
    case 'integer':
    case 'number':
      return 0
    case 'boolean':
      return false
    case 'array':
      return []
    case 'object':
      return {}
    default:
      return ''
  }
}

/** 键值行新增键名：`新字段`、`新字段2`… 依次避让既有键。 */
export function nextKeyName(existing: string[], base = '新字段'): string {
  if (!existing.includes(base)) return base
  let index = 2
  while (existing.includes(`${base}${index}`)) index += 1
  return `${base}${index}`
}

/**
 * 精确匹配 pointer 的诊断（M2 Diagnostic[] → 字段级）。
 * 未带 pointer 的诊断按根（''）命中，落表单根下汇总展示。
 */
export function diagnosticsAt(diagnostics: Diagnostic[] | undefined, pointer: string): Diagnostic[] {
  return (diagnostics ?? []).filter((diagnostic) => (diagnostic.loc.pointer ?? '') === pointer)
}
