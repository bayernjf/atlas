/**
 * 用例参数化向导纯逻辑（04 §5.11，2026-09-29）。
 * 把录制用例的 inputs 按顶层键展开成可填空字段，再组装成 inputs_override。
 * 留空字段一律省略；number/json 字段非法即报错。
 */

export type ParamKind = 'string' | 'number' | 'boolean' | 'json'

export interface ParamField {
  key: string
  kind: ParamKind
  draft: string
}

export function kindOfValue(value: unknown): ParamKind {
  if (typeof value === 'number') return 'number'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'string') return 'string'
  return 'json'
}

export function initialDraft(kind: ParamKind, value: unknown): string {
  if (kind === 'json') return JSON.stringify(value)
  return String(value)
}

export function fieldsFromInputs(inputs: Record<string, unknown>): ParamField[] {
  return Object.keys(inputs).map((key) => {
    const kind = kindOfValue(inputs[key])
    return { key, kind, draft: initialDraft(kind, inputs[key]) }
  })
}

export interface ParamOverrideResult {
  ok: boolean
  value?: Record<string, unknown>
  errorKey?: string
  errorCode?: 'not_a_number' | 'invalid_json'
}

export function overrideFromFields(fields: ParamField[]): ParamOverrideResult {
  const value: Record<string, unknown> = {}
  for (const field of fields) {
    const draft = field.draft.trim()
    if (field.kind === 'boolean') {
      value[field.key] = draft === 'true'
      continue
    }
    if (draft === '') continue
    if (field.kind === 'string') {
      value[field.key] = field.draft
      continue
    }
    if (field.kind === 'number') {
      const n = Number(draft)
      if (!Number.isFinite(n)) {
        return { ok: false, errorKey: field.key, errorCode: 'not_a_number' }
      }
      value[field.key] = n
      continue
    }
    try {
      value[field.key] = JSON.parse(draft)
    } catch {
      return { ok: false, errorKey: field.key, errorCode: 'invalid_json' }
    }
  }
  return { ok: true, value }
}
