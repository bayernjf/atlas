/**
 * MetaSchema：节点 config 声明式 Data Schema 的类型与形状自检。
 *
 * 04 §4.9「前端 MetaSchema 扩展」（M1）：与后端 Capability schema 构造期校验
 * 同源 20 个 JSON Schema 白名单 keyword，外加四个仅前端设计态的 x-* keyword。
 * 后端 Capability input_schema/output_schema 仍拒绝一切 x-*，本模块不参与后端校验。
 */

export const META_SCHEMA_KEYWORDS = [
  'type',
  'properties',
  'required',
  'items',
  'additionalProperties',
  'enum',
  'const',
  'oneOf',
  'description',
  'default',
  'minimum',
  'maximum',
  'exclusiveMinimum',
  'exclusiveMaximum',
  'minItems',
  'maxItems',
  'minLength',
  'maxLength',
  'pattern',
] as const

export const SCHEMA_TYPES = [
  'string',
  'integer',
  'number',
  'boolean',
  'object',
  'array',
  'null',
] as const

export type SchemaType = (typeof SCHEMA_TYPES)[number]

export type XRef = {
  /** 允许的节点种类；缺省或含 '*' 表示任意种类。 */
  kinds?: string[]
}

export type MetaSchema = {
  type?: SchemaType
  properties?: Record<string, MetaSchema>
  required?: string[]
  items?: MetaSchema
  additionalProperties?: boolean | MetaSchema
  enum?: unknown[]
  const?: unknown
  oneOf?: MetaSchema[]
  description?: string
  default?: unknown
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number | boolean
  exclusiveMaximum?: number | boolean
  minItems?: number
  maxItems?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  'x-variable'?: boolean
  'x-widget'?: string
  'x-ref'?: XRef
  'x-outputSchema'?: MetaSchema
  [keyword: string]: unknown
}

/** 九类内置节点的 config schema 根：object schema + 强制 x-outputSchema。 */
export type NodeConfigSchema = MetaSchema & {
  type: 'object'
  'x-outputSchema': MetaSchema
}

const FRONTEND_X_KEYWORDS = ['x-variable', 'x-widget', 'x-ref', 'x-outputSchema'] as const

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fail(path: string, message: string): never {
  throw new Error(`非法 MetaSchema（位置 ${path}）：${message}`)
}

/**
 * 递归形状自检。options.output=true 时为 x-outputSchema 内部：只允许同源 20 个
 * 普通 keyword（outputs 是运行期数据形状，不允许设计态 x-* 标注）。
 */
export function assertMetaSchema(
  schema: unknown,
  path = '$',
  options: { root?: boolean; output?: boolean } = {},
): void {
  if (!isPlainObject(schema)) fail(path, '必须是对象')

  for (const keyword of Object.keys(schema)) {
    const at = `${path}.${keyword}`
    if (!META_SCHEMA_KEYWORDS.includes(keyword as (typeof META_SCHEMA_KEYWORDS)[number])) {
      if (FRONTEND_X_KEYWORDS.includes(keyword as (typeof FRONTEND_X_KEYWORDS)[number])) {
        if (options.output) fail(at, 'x-outputSchema 内部不允许 x-* keyword')
        if (keyword === 'x-outputSchema' && !options.root) {
          fail(at, 'x-outputSchema 只允许出现在节点 schema 根')
        }
      } else {
        fail(at, `白名单外 keyword "${keyword}"`)
      }
    }
  }

  if (schema.type !== undefined) {
    if (!SCHEMA_TYPES.includes(schema.type as SchemaType)) fail(`${path}.type`, `非法 type "${schema.type}"`)
  }
  if (schema.properties !== undefined) {
    if (!isPlainObject(schema.properties)) fail(`${path}.properties`, '必须是对象')
    for (const [name, sub] of Object.entries(schema.properties)) {
      assertMetaSchema(sub, `${path}.properties.${name}`, { output: options.output })
    }
  }
  if (schema.required !== undefined) {
    if (!Array.isArray(schema.required) || !schema.required.every((key) => typeof key === 'string')) {
      fail(`${path}.required`, '必须是字符串数组')
    }
    if (isPlainObject(schema.properties)) {
      for (const key of schema.required as string[]) {
        if (!(key in schema.properties)) fail(`${path}.required`, `required 字段 "${key}" 未在 properties 声明`)
      }
    }
  }
  if (schema.items !== undefined) assertMetaSchema(schema.items, `${path}.items`, { output: options.output })
  if (schema.additionalProperties !== undefined) {
    if (typeof schema.additionalProperties !== 'boolean' && !isPlainObject(schema.additionalProperties)) {
      fail(`${path}.additionalProperties`, '必须是 boolean 或子 schema 对象')
    }
    if (isPlainObject(schema.additionalProperties)) {
      assertMetaSchema(schema.additionalProperties, `${path}.additionalProperties`, { output: options.output })
    }
  }
  if (schema.enum !== undefined) {
    if (!Array.isArray(schema.enum)) fail(`${path}.enum`, '必须是数组')
  }
  if (schema.oneOf !== undefined) {
    if (!Array.isArray(schema.oneOf) || !schema.oneOf.every(isPlainObject)) {
      fail(`${path}.oneOf`, '必须是子 schema 对象数组')
    }
    schema.oneOf.forEach((sub, index) => {
      assertMetaSchema(sub, `${path}.oneOf[${index}]`, { output: options.output })
    })
  }
  for (const keyword of ['minimum', 'maximum', 'minItems', 'maxItems', 'minLength', 'maxLength'] as const) {
    if (schema[keyword] !== undefined && typeof schema[keyword] !== 'number') {
      fail(`${path}.${keyword}`, '必须是数字')
    }
  }
  for (const keyword of ['exclusiveMinimum', 'exclusiveMaximum'] as const) {
    if (schema[keyword] !== undefined) {
      const value = schema[keyword]
      if (typeof value !== 'number' && typeof value !== 'boolean') {
        fail(`${path}.${keyword}`, '必须是数字或 boolean')
      }
    }
  }
  if (schema.pattern !== undefined) {
    if (typeof schema.pattern !== 'string') fail(`${path}.pattern`, '必须是字符串')
    try {
      new RegExp(schema.pattern)
    } catch {
      fail(`${path}.pattern`, `非法正则 "${schema.pattern}"`)
    }
  }
  if (!options.output) {
    if (schema['x-variable'] !== undefined && typeof schema['x-variable'] !== 'boolean') {
      fail(`${path}.x-variable`, '必须是 boolean')
    }
    if (schema['x-widget'] !== undefined) {
      if (typeof schema['x-widget'] !== 'string' || schema['x-widget'].length === 0) {
        fail(`${path}.x-widget`, '必须是非空 string')
      }
    }
    if (schema['x-ref'] !== undefined) {
      if (!isPlainObject(schema['x-ref'])) fail(`${path}.x-ref`, '必须是对象')
      const kinds = (schema['x-ref'] as XRef).kinds
      if (kinds !== undefined) {
        if (!Array.isArray(kinds) || !kinds.every((kind) => typeof kind === 'string' && kind.length > 0)) {
          fail(`${path}.x-ref.kinds`, '必须是非空字符串数组')
        }
      }
    }
    if (options.root) {
      if (!isPlainObject(schema['x-outputSchema'])) fail(`${path}.x-outputSchema`, '节点 schema 根必须声明对象形态的 x-outputSchema')
      assertMetaSchema(schema['x-outputSchema'], `${path}.x-outputSchema`, { output: true })
    }
  }
}

/** 节点 config schema 根自检：必须是带 x-outputSchema 的 object schema。 */
export function assertNodeConfigSchema(schema: unknown): asserts schema is NodeConfigSchema {
  assertMetaSchema(schema, '$', { root: true })
  if ((schema as MetaSchema).type !== 'object') fail('$.type', '节点 config schema 根必须是 object')
}
