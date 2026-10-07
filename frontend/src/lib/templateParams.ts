/**
 * 模板参数化向导纯逻辑（打包 A1，docs/97 §3.4；U1188）。
 *
 * 职责：由模板 params 声明（JSON Schema 子集，见 apiClient.TemplateParam）派生
 * 表单字段与初值；不做任何渲染/网络 IO，便于 vitest 单测。
 */

import type { TemplateParams } from './apiClient'

export type ParamField = {
  name: string
  label: string
  type: 'string' | 'number' | 'boolean' | 'select'
  required: boolean
  default?: string | number | boolean
  hint?: string
  options?: string[]
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
  }))
}

/** 表单初值：声明 default 优先；select 无 default 取首个 option；其余 undefined（required 由提交时校验）。 */
export function buildInitialValues(fields: ParamField[]): Record<string, string | number | boolean> {
  const values: Record<string, string | number | boolean> = {}
  for (const field of fields) {
    if (field.default !== undefined) {
      values[field.name] = field.default
    } else if (field.type === 'select' && field.options && field.options.length > 0) {
      values[field.name] = field.options[0]
    }
  }
  return values
}

/** 提交前客户端校验（与后端 422 语义对齐：required 缺失/数字类型/布尔类型/select 范围）。 */
export function validateParamValues(
  fields: ParamField[],
  values: Record<string, string | number | boolean>,
): string[] {
  const errors: string[] = []
  for (const field of fields) {
    const present = values[field.name] !== undefined && values[field.name] !== ''
    if (field.required && !present) {
      errors.push(`${field.label}（${field.name}）为必填`)
      continue
    }
    if (!present) continue
    const value = values[field.name]
    if (field.type === 'number' && typeof value !== 'number') {
      errors.push(`${field.label}（${field.name}）必须是数字`)
    } else if (field.type === 'boolean' && typeof value !== 'boolean') {
      errors.push(`${field.label}（${field.name}）必须是布尔值`)
    } else if (
      field.type === 'select' &&
      field.options &&
      !field.options.includes(String(value))
    ) {
      errors.push(`${field.label}（${field.name}）的值不在可选范围内`)
    }
  }
  return errors
}
