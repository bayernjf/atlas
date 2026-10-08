/**
 * A1 模板参数声明 → forms 内核 MetaSchema/UiSchema 桥接（打包 ZW，docs/105 §2.1；U1245 起；
 * 打包 ZX，docs/106 §2.3 递归扩展）。
 *
 * 职责：把模板市场参数化向导的 TemplateParam 声明（apiClient.TemplateParams）映射为
 * forms 内核消费的 MetaSchema（root object + properties + required + default/enum）与
 * UiSchema（labels/hints 承接中文文案、visibleWhen → hiddenWhen 条件显隐），使「模板
 * 参数」成为第三类吃 FormRenderer 的实体（节点 config、工具 params 之后）。纯逻辑、
 * 零 React、零网络 IO，便于 vitest 单测。
 *
 * 映射规则（docs/105 §2.1 表 + docs/106 §2.3 扩展）：
 * - string → { type: 'string', default? }
 * - number → { type: 'number', default? }
 * - boolean → { type: 'boolean', default? }
 * - select  → { type: 'string', enum: options, default? }（enum 驱动 select 控件）
 * - object  → { type: 'object', properties: 递归, default? }（嵌套 group 渲染）
 * - array   → { type: 'array', items: 递归, minItems?, maxItems?, default? }（增删行渲染）
 * - root：{ type: 'object', properties, required: [...根层 required 字段] }
 * - label → uiSchema.labels[name]（缺省显示字段名）；hint → uiSchema.hints[name]（缺省不渲染）。
 * - visibleWhen → uiSchema.hiddenWhen：{ field, equals, show: [本字段名] }（条件显隐，根层
 *   判别字段相对根 object；嵌套 object 内字段的判别字段相对其所在 object）。
 *
 * 不映射 options 之外的自定义键；default 只透传合法类型。初值（buildInitialValues）与
 * 提交校验（validateParamValues）仍在 lib/templateParams 保留，本模块不承担。
 */

import type { MetaSchema } from '../schemas/metaSchema'
import type { TemplateParam, TemplateParams } from '../apiClient'
import type { UiSchema, UiHiddenWhen } from './uiSchema'

/** 单个 TemplateParam 声明 → MetaSchema（含结构化递归）。 */
function paramToSchema(decl: TemplateParam): MetaSchema {
  const property: MetaSchema = { type: decl.type === 'select' ? 'string' : decl.type }
  if (decl.type === 'select' && Array.isArray(decl.options) && decl.options.length > 0) {
    property.enum = [...decl.options]
  }
  if (decl.type === 'object' && decl.properties) {
    const sub = paramGroupToSchema(decl.properties)
    property.properties = sub.properties
    if (sub.required && sub.required.length > 0) property.required = sub.required
  }
  if (decl.type === 'array' && decl.items) {
    property.items = paramToSchema(decl.items)
    if (typeof decl.minItems === 'number') property.minItems = decl.minItems
    if (typeof decl.maxItems === 'number') property.maxItems = decl.maxItems
  }
  if (decl.default !== undefined) property.default = decl.default
  return property
}

/** 一组 params 声明 → object MetaSchema（properties + 局部 required）。 */
function paramGroupToSchema(params: TemplateParams): MetaSchema {
  const properties: Record<string, MetaSchema> = {}
  const required: string[] = []
  for (const [name, decl] of Object.entries(params)) {
    properties[name] = paramToSchema(decl)
    if (decl.required) required.push(name)
  }
  const schema: MetaSchema = { type: 'object', properties }
  if (required.length > 0) schema.required = required
  return schema
}

/**
 * TemplateParam 声明 → MetaSchema（root object schema）。
 *
 * select 无 options 时降级为普通 string（与 A1 buildParamFields 的 select 无 options
 * 行为对齐）；字段无声明键一律跳过（模板 params 声明是权威白名单）。
 */
export function templateParamsToMetaSchema(params: TemplateParams | undefined): MetaSchema {
  return paramGroupToSchema(params ?? {})
}

/**
 * TemplateParam 声明 → UiSchema（labels/hints 承接中文文案；visibleWhen → hiddenWhen）。
 *
 * label 缺省不写入（FormRenderer 回退字段名，与 A1 现行为一致）；hint 缺省不写入
 * （Field 不渲染说明行）。嵌套字段文案键照 uiSchema 通配规则：object 子字段
 * `parent.child`；数组元素 `name[].sub`（[] 匹配任一下标）。visibleWhen 的判别字段
 * 相对本字段所在 object（根层即根参数名），与 hiddenWhen 判别语义一致。
 */
export function templateParamsToUiSchema(params: TemplateParams | undefined): UiSchema {
  const labels: Record<string, string> = {}
  const hints: Record<string, string> = {}
  const hiddenWhen: UiHiddenWhen[] = []
  walk(params ?? {}, '', labels, hints, hiddenWhen)
  const uiSchema: UiSchema = {}
  if (Object.keys(labels).length > 0) uiSchema.labels = labels
  if (Object.keys(hints).length > 0) uiSchema.hints = hints
  if (hiddenWhen.length > 0) uiSchema.hiddenWhen = hiddenWhen
  return uiSchema
}

function walk(
  params: TemplateParams,
  prefix: string,
  labels: Record<string, string>,
  hints: Record<string, string>,
  hiddenWhen: UiHiddenWhen[],
  nested = false,
): void {
  for (const [name, decl] of Object.entries(params)) {
    const key = prefix ? `${prefix}${name}` : name
    if (decl.label) labels[key] = decl.label
    if (decl.hint) hints[key] = decl.hint
    if (decl.visibleWhen) {
      hiddenWhen.push(
        nested
          ? // 嵌套 object 内子字段：判别字段相对根 record（与 hiddenWhen rootScoped 语义一致）
            { field: decl.visibleWhen.field, equals: decl.visibleWhen.equals, show: [name], rootScoped: true }
          : { field: decl.visibleWhen.field, equals: decl.visibleWhen.equals, show: [name] },
      )
    }
    if (decl.type === 'object' && decl.properties) {
      walk(decl.properties, `${key}.`, labels, hints, hiddenWhen, true)
    } else if (decl.type === 'array' && decl.items) {
      walkItem(decl.items, `${key}[].`, labels, hints)
    }
  }
}

function walkItem(
  item: TemplateParam,
  prefix: string,
  labels: Record<string, string>,
  hints: Record<string, string>,
): void {
  // 数组元素级 label/hint（元素 object 内子字段；元素本身是标量时其文案由根声明 label 承接）
  if (item.type === 'object' && item.properties) {
    walk(item.properties, prefix, labels, hints, [])
  } else if (item.type === 'array' && item.items) {
    walkItem(item.items, `${prefix}[]`, labels, hints)
  }
}
