/**
 * A1 模板参数声明 → forms 内核 MetaSchema/UiSchema 桥接（打包 ZW，docs/105 §2.1；U1245 起）。
 *
 * 职责：把模板市场参数化向导的 TemplateParam 声明（apiClient.TemplateParams）映射为
 * forms 内核消费的 MetaSchema（root object + properties + required + default/enum）与
 * UiSchema（labels/hints 承接中文文案），使「模板参数」成为第三类吃 FormRenderer 的
 * 实体（节点 config、工具 params 之后）。纯逻辑、零 React、零网络 IO，便于 vitest 单测。
 *
 * 映射规则（docs/105 §2.1 表）：
 * - string → { type: 'string', default? }
 * - number → { type: 'number', default? }
 * - boolean → { type: 'boolean', default? }
 * - select  → { type: 'string', enum: options, default? }（enum 驱动 select 控件）
 * - root：{ type: 'object', properties, required: [...required 字段] }
 * - label → uiSchema.labels[name]（缺省显示字段名）；hint → uiSchema.hints[name]（缺省不渲染）。
 *
 * 不映射 options 之外的自定义键；default 只透传合法类型。初值（buildInitialValues）与
 * 提交校验（validateParamValues）仍在 lib/templateParams 保留，本模块不承担。
 */

import type { MetaSchema } from '../schemas/metaSchema'
import type { TemplateParams } from '../apiClient'
import type { UiSchema } from './uiSchema'

/**
 * TemplateParam 声明 → MetaSchema（root object schema）。
 *
 * select 无 options 时降级为普通 string（与 A1 buildParamFields 的 select 无 options
 * 行为对齐）；字段无声明键一律跳过（模板 params 声明是权威白名单）。
 */
export function templateParamsToMetaSchema(params: TemplateParams | undefined): MetaSchema {
  const entries = Object.entries(params ?? {})
  const properties: Record<string, MetaSchema> = {}
  const required: string[] = []
  for (const [name, decl] of entries) {
    const property: MetaSchema = { type: decl.type === 'select' ? 'string' : decl.type }
    if (decl.type === 'select' && Array.isArray(decl.options) && decl.options.length > 0) {
      property.enum = [...decl.options]
    }
    if (decl.default !== undefined) property.default = decl.default
    properties[name] = property
    if (decl.required) required.push(name)
  }
  const schema: MetaSchema = { type: 'object', properties }
  if (required.length > 0) schema.required = required
  return schema
}

/**
 * TemplateParam 声明 → UiSchema（labels/hints 承接中文文案）。
 *
 * label 缺省不写入（FormRenderer 回退字段名，与 A1 现行为一致）；hint 缺省不写入
 * （Field 不渲染说明行）。
 */
export function templateParamsToUiSchema(params: TemplateParams | undefined): UiSchema {
  const labels: Record<string, string> = {}
  const hints: Record<string, string> = {}
  for (const [name, decl] of Object.entries(params ?? {})) {
    if (decl.label) labels[name] = decl.label
    if (decl.hint) hints[name] = decl.hint
  }
  const uiSchema: UiSchema = {}
  if (Object.keys(labels).length > 0) uiSchema.labels = labels
  if (Object.keys(hints).length > 0) uiSchema.hints = hints
  return uiSchema
}
