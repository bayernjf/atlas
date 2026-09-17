/**
 * resolveWidget：按 04 §4.10 选择序把 schema 片段解析为控件或结构容器（M3）。
 *
 * 选择序：① 节点 schema 的 x-widget（工具 Capability schema 拒绝一切 x-*，
 * source='tool' 时忽略）；② 类型结构默认；③ 白名单外结构一律降级 json。
 * object/array 不是注册控件，返回结构容器由 FormRenderer 递归/增删行承接。
 */
import type { MetaSchema } from '../schemas/metaSchema'
import type { WidgetName } from './types'

export type SchemaSource = 'node' | 'tool'

export type WidgetResolution =
  | { kind: 'widget'; widget: WidgetName }
  | { kind: 'group' }
  | { kind: 'array' }
  | { kind: 'keyvalue' }

function isObjectSchema(schema: MetaSchema): boolean {
  return schema.type === 'object' || (!schema.type && !!schema.properties)
}

export function resolveWidget(schema: MetaSchema, source: SchemaSource = 'node'): WidgetResolution {
  if (source === 'node' && typeof schema['x-widget'] === 'string' && schema['x-widget']) {
    return { kind: 'widget', widget: schema['x-widget'] as WidgetName }
  }

  // 有统一 properties 的 object 即便带顶层 oneOf 判别联合（如 trigger：按 triggerType
  // 三分支条件必填）也按 properties 展开成 group；oneOf 只承载条件必填，结构由 L1 解释。
  // 无统一 properties 的 oneOf 联合（如 to: string|array）无法生成稳定字段，才降级 json。
  if (isObjectSchema(schema) && schema.properties && Object.keys(schema.properties).length > 0) {
    return { kind: 'group' }
  }
  if (Array.isArray(schema.oneOf) && schema.oneOf.length > 0) {
    return { kind: 'widget', widget: 'json' }
  }
  if (schema.enum || schema.const !== undefined) {
    return { kind: 'widget', widget: 'select' }
  }
  switch (schema.type) {
    case 'boolean':
      return { kind: 'widget', widget: 'switch' }
    case 'integer':
    case 'number':
      return { kind: 'widget', widget: 'number' }
    case 'array':
      return schema.items ? { kind: 'array' } : { kind: 'widget', widget: 'json' }
    case 'object':
      return resolveObject(schema)
    case 'string':
      return schema['x-variable'] === true
        ? { kind: 'widget', widget: 'variable-input' }
        : { kind: 'widget', widget: 'text' }
    case 'null':
    case undefined:
      if (isObjectSchema(schema)) return resolveObject(schema)
      return { kind: 'widget', widget: 'json' }
    default:
      return { kind: 'widget', widget: 'json' }
  }
}

function resolveObject(schema: MetaSchema): WidgetResolution {
  if (schema.properties && Object.keys(schema.properties).length > 0) {
    return { kind: 'group' }
  }
  // 无 properties 但声明 additionalProperties（http headers 形态）→ 键值行；
  // additionalProperties:false 或缺省的无类型对象无法生成表单 → 降级 json。
  if (schema.additionalProperties) {
    return { kind: 'keyvalue' }
  }
  return { kind: 'widget', widget: 'json' }
}
