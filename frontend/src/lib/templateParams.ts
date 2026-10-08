/**
 * 模板参数化向导纯逻辑（打包 A1，docs/97 §3.4；U1188；打包 ZX，docs/106 §2.5 结构化递归）。
 *
 * 职责：由模板 params 声明（JSON Schema 子集，见 apiClient.TemplateParam）派生
 * 表单字段与初值；不做任何渲染/网络 IO，便于 vitest 单测。
 *
 * 打包 ZX：ParamField 支持结构化声明（object/array），buildParamFields 顶层展开
 * （嵌套子字段不进数组，Editor 计数 Alert 按顶层声明数）；buildInitialValues 对
 * object/array 无 default 给空容器；validateParamValues 递归校验嵌套值与后端 422 语义对齐。
 */

import type { TemplateParam, TemplateParams } from './apiClient'

export type ParamField = {
  name: string
  label: string
  type: 'string' | 'number' | 'boolean' | 'select' | 'object' | 'array'
  required: boolean
  default?: unknown
  hint?: string
  options?: string[]
  // 打包 ZX：结构化声明透传（递归校验/初值用）
  properties?: TemplateParams
  items?: TemplateParam
  minItems?: number
  maxItems?: number
}

export function buildParamFields(params: TemplateParams): ParamField[] {
  return Object.entries(params ?? {}).map(([name, decl]) => ({
    name,
    label: decl.label || name,
    type: decl.type ?? 'string',
    required: decl.required ?? false,
    default: decl.default,
    hint: decl.hint,
    options: decl.type === 'select' ? decl.options ?? [] : undefined,
    properties: decl.type === 'object' ? decl.properties : undefined,
    items: decl.type === 'array' ? decl.items : undefined,
    minItems: decl.type === 'array' ? decl.minItems : undefined,
    maxItems: decl.type === 'array' ? decl.maxItems : undefined,
  }))
}

/** 表单初值：声明 default 优先；select 无 default 取首个 option；object/array 无 default 给空容器；其余 undefined（required 由提交时校验）。 */
export function buildInitialValues(fields: ParamField[]): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  for (const field of fields) {
    if (field.default !== undefined) {
      values[field.name] = field.default
    } else if (field.type === 'select' && field.options && field.options.length > 0) {
      values[field.name] = field.options[0]
    } else if (field.type === 'object') {
      values[field.name] = {}
    } else if (field.type === 'array') {
      values[field.name] = []
    }
  }
  return values
}

/** 提交前客户端校验（与后端 422 语义对齐：required 缺失/数字类型/布尔类型/select 范围/object 子字段/array 元素）。 */
export function validateParamValues(
  fields: ParamField[],
  values: Record<string, unknown>,
): string[] {
  const errors: string[] = []
  for (const field of fields) {
    const present = values[field.name] !== undefined && values[field.name] !== ''
    if (field.required && !present) {
      errors.push(`${field.label}（${field.name}）为必填`)
      continue
    }
    if (!present) continue
    validateFieldValue(field, values[field.name], errors)
  }
  return errors
}

function validateFieldValue(field: ParamField, value: unknown, errors: string[]): void {
  if (field.type === 'number') {
    if (typeof value !== 'number') {
      errors.push(`${field.label}（${field.name}）必须是数字`)
    }
  } else if (field.type === 'boolean') {
    if (typeof value !== 'boolean') {
      errors.push(`${field.label}（${field.name}）必须是布尔值`)
    }
  } else if (field.type === 'select') {
    if (field.options && !field.options.includes(String(value))) {
      errors.push(`${field.label}（${field.name}）的值不在可选范围内`)
    }
  } else if (field.type === 'object') {
    if (!Array.isArray(value) && typeof value === 'object' && value !== null) {
      const record = value as Record<string, unknown>
      const subParams = field.properties ?? {}
      for (const [subName, subDecl] of Object.entries(subParams)) {
        const subPresent = record[subName] !== undefined && record[subName] !== ''
        if (subDecl.required && !subPresent) {
          errors.push(`${field.label}.${subName} 为必填`)
        } else if (subPresent) {
          validateFieldValue(
            { name: `${field.name}.${subName}`, label: subDecl.label || subName, type: subDecl.type ?? 'string', required: false, default: subDecl.default, hint: subDecl.hint, options: subDecl.type === 'select' ? subDecl.options : undefined, properties: subDecl.type === 'object' ? subDecl.properties : undefined, items: subDecl.type === 'array' ? subDecl.items : undefined, minItems: subDecl.type === 'array' ? subDecl.minItems : undefined, maxItems: subDecl.type === 'array' ? subDecl.maxItems : undefined },
            record[subName],
            errors,
          )
        }
      }
    } else {
      errors.push(`${field.label}（${field.name}）必须是对象`)
    }
  } else if (field.type === 'array') {
    if (Array.isArray(value)) {
      const items = value as unknown[]
      if (typeof field.minItems === 'number' && items.length < field.minItems) {
        errors.push(`${field.label}（${field.name}）至少需要 ${field.minItems} 项`)
      }
      if (typeof field.maxItems === 'number' && items.length > field.maxItems) {
        errors.push(`${field.label}（${field.name}）最多允许 ${field.maxItems} 项`)
      }
      if (field.items) {
        for (const [index, item] of items.entries()) {
          validateFieldValue(
            { name: `${field.name}[${index}]`, label: `${field.label}[${index}]`, type: field.items.type ?? 'string', required: false, default: field.items.default, hint: field.items.hint, options: field.items.type === 'select' ? field.items.options : undefined, properties: field.items.type === 'object' ? field.items.properties : undefined, items: field.items.type === 'array' ? field.items.items : undefined, minItems: field.items.type === 'array' ? field.items.minItems : undefined, maxItems: field.items.type === 'array' ? field.items.maxItems : undefined },
            item,
            errors,
          )
        }
      }
    } else {
      errors.push(`${field.label}（${field.name}）必须是数组`)
    }
  }
}
