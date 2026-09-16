/**
 * 从声明式 MetaSchema 派生节点 config 的 L1 字段诊断（M1，04 §4.9）。
 *
 * 仅解释九份节点 schema 实际用到的同源白名单子集；x-* 为设计态标注，
 * 不产生 L1 诊断（x-ref 的图级存在性在 L3，x-widget 由 M3 消费）。
 * 不引 AJV；完整 L1 校验器形态由 ADR T16 在 M2 前收口。
 */

import type { MetaSchema } from './metaSchema'

export type SchemaDiagnostic = {
  /** config 相对路径，如 cron / branches[0].label / inputs.order_id。 */
  path: string
  rule: 'type' | 'required' | 'enum' | 'const' | 'range' | 'length' | 'pattern' | 'minItems' | 'maxItems' | 'additionalProperties' | 'oneOf'
}

const DISCRIMINATOR_RULES = new Set<SchemaDiagnostic['rule']>(['type', 'enum', 'const'])

function joinPath(base: string, key: string | number): string {
  return typeof key === 'number' ? `${base}[${key}]` : base ? `${base}.${key}` : key
}

function checkType(schema: MetaSchema, value: unknown): boolean {
  if (value === undefined) return true
  switch (schema.type) {
    case 'string':
      return typeof value === 'string'
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value)
    case 'number':
      return typeof value === 'number'
    case 'boolean':
      return typeof value === 'boolean'
    case 'null':
      return value === null
    case 'array':
      return Array.isArray(value)
    case 'object':
      return typeof value === 'object' && value !== null && !Array.isArray(value)
    default:
      return true
  }
}

function validateBranch(schema: MetaSchema, value: unknown, path: string, out: SchemaDiagnostic[]): void {
  if (value === undefined || value === null) return

  if (!checkType(schema, value)) {
    out.push({ path, rule: 'type' })
    return
  }
  if (schema.enum !== undefined && !schema.enum.some((candidate) => candidate === value)) {
    out.push({ path, rule: 'enum' })
  }
  if (schema.const !== undefined && schema.const !== value) {
    out.push({ path, rule: 'const' })
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined) {
      const exclusive = schema.exclusiveMinimum === true
      if ((exclusive ? value <= schema.minimum : value < schema.minimum) ||
        (typeof schema.exclusiveMinimum === 'number' && value <= schema.exclusiveMinimum)) {
        out.push({ path, rule: 'range' })
      }
    }
    if (schema.maximum !== undefined) {
      const exclusive = schema.exclusiveMaximum === true
      if ((exclusive ? value >= schema.maximum : value > schema.maximum) ||
        (typeof schema.exclusiveMaximum === 'number' && value >= schema.exclusiveMaximum)) {
        out.push({ path, rule: 'range' })
      }
    }
  }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) out.push({ path, rule: 'length' })
    if (schema.maxLength !== undefined && value.length > schema.maxLength) out.push({ path, rule: 'length' })
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
      out.push({ path, rule: 'pattern' })
    }
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) out.push({ path, rule: 'minItems' })
    if (schema.maxItems !== undefined && value.length > schema.maxItems) out.push({ path, rule: 'maxItems' })
    if (schema.items) {
      value.forEach((item, index) => validateBranch(schema.items!, item, joinPath(path, index), out))
    }
  }
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const record = value as Record<string, unknown>
    if (Array.isArray(schema.required)) {
      for (const key of schema.required) {
        if (record[key] === undefined) out.push({ path: joinPath(path, key), rule: 'required' })
      }
    }
    if (schema.properties) {
      for (const [key, sub] of Object.entries(schema.properties)) {
        if (key in record) validateBranch(sub, record[key], joinPath(path, key), out)
      }
    }
    if (schema.additionalProperties !== undefined) {
      const declared = new Set(Object.keys(schema.properties ?? {}))
      for (const key of Object.keys(record)) {
        if (declared.has(key)) continue
        if (schema.additionalProperties === false) {
          out.push({ path: joinPath(path, key), rule: 'additionalProperties' })
        } else if (typeof schema.additionalProperties === 'object') {
          validateBranch(schema.additionalProperties, record[key], joinPath(path, key), out)
        }
      }
    }
  }
}

function dedupe(diagnostics: SchemaDiagnostic[]): SchemaDiagnostic[] {
  const seen = new Set<string>()
  return diagnostics.filter((diagnostic) => {
    const key = `${diagnostic.path}${diagnostic.rule}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/**
 * 按白名单子集校验 config；返回字段路径 + 规则诊断（顺序稳定）。
 * oneOf 采用判别分支策略：enum/const/type 全部通过的分支才视为候选，
 * 仅聚合候选分支上的 required/range/pattern 等叶子错误；无候选时聚合判别错误。
 */
export function validateConfigBySchema(schema: MetaSchema, config: unknown): SchemaDiagnostic[] {
  const out: SchemaDiagnostic[] = []

  if (Array.isArray(schema.oneOf)) {
    const branchResults = schema.oneOf.map((branch) => {
      const branchErrors: SchemaDiagnostic[] = []
      validateBranch(branch, config, '', branchErrors)
      return dedupe(branchErrors)
    })
    const candidates = branchResults.filter((errors) => errors.every((error) => !DISCRIMINATOR_RULES.has(error.rule)))
    if (candidates.length > 0) {
      out.push(...candidates.flat())
    } else {
      out.push(...branchResults.flat())
    }
  }

  const base: SchemaDiagnostic[] = []
  const withoutOneOf = { ...schema, oneOf: undefined }
  validateBranch(withoutOneOf, config, '', base)
  out.push(...base)

  const result = dedupe(out)
  result.sort((a, b) => (a.path === b.path ? a.rule.localeCompare(b.rule) : a.path.localeCompare(b.path)))
  return result
}
