/**
 * 消息模板表单校验纯逻辑（打包 A2，docs/98 §2.3；U1195）。
 *
 * 与后端 `atlas.message.template_store.validate_message_template` 同规则：
 * kind 枚举、name ≤64、subject ≤200、body ≤4000、variables 白名单/去重/≤20、
 * 正文占位 ⊆ variables 声明。前端即时校验给出中文提示，提交仍以后端为准。
 */

export const MESSAGE_TEMPLATE_KINDS = ['approval', 'alert'] as const
export type MessageTemplateKind = (typeof MESSAGE_TEMPLATE_KINDS)[number]

export const MESSAGE_TEMPLATE_KIND_LABELS: Record<MessageTemplateKind, string> = {
  approval: '审批通知',
  alert: '告警通知',
}

const VAR_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/
const PLACEHOLDER_RE = /\{\{\s*([^{}]+?)\s*\}\}/g
export const MAX_VARIABLES = 20

export function extractPlaceholders(text: string): string[] {
  const seen: string[] = []
  for (const match of text.matchAll(PLACEHOLDER_RE)) {
    const name = match[1].trim()
    if (name && !seen.includes(name)) {
      seen.push(name)
    }
  }
  return seen
}

/** 逗号/换行/空白分隔的输入文本 → 去重变量数组（空条目忽略）。 */
export function parseVariablesInput(text: string): string[] {
  const names = text
    .split(/[\s,，、\n]+/)
    .map((part) => part.trim())
    .filter(Boolean)
  return [...new Set(names)]
}

export function validateVariables(variables: string[]): string[] {
  const errors: string[] = []
  if (variables.length > MAX_VARIABLES) {
    errors.push(`模板变量声明不能超过 ${MAX_VARIABLES} 个`)
  }
  const seen = new Set<string>()
  for (const variable of variables) {
    if (!VAR_NAME_RE.test(variable)) {
      errors.push(`变量名 ${variable} 不合法（须 [A-Za-z_][A-Za-z0-9_]*）`)
    } else if (seen.has(variable)) {
      errors.push(`变量名重复：${variable}`)
    } else {
      seen.add(variable)
    }
  }
  return errors
}

export function validateMessageTemplate(input: {
  name: string
  kind: string
  subject: string
  body: string
  variables: string[]
}): string[] {
  const errors: string[] = []
  const name = input.name.trim()
  const kind = input.kind as MessageTemplateKind
  if (!MESSAGE_TEMPLATE_KINDS.includes(kind)) {
    errors.push('kind 必须是 approval 或 alert')
  }
  if (!name) {
    errors.push('模板名称不能为空')
  } else if (name.length > 64) {
    errors.push('模板名称长度须在 64 字符以内')
  }
  if (!input.subject.trim()) {
    errors.push('邮件主题不能为空')
  } else if (input.subject.length > 200) {
    errors.push('邮件主题长度须在 200 字符以内')
  }
  if (!input.body.trim()) {
    errors.push('正文不能为空')
  } else if (input.body.length > 4000) {
    errors.push('正文长度须在 4000 字符以内')
  }
  errors.push(...validateVariables(input.variables))
  const declared = new Set(input.variables)
  for (const [partName, part] of [
    ['邮件主题', input.subject],
    ['正文', input.body],
  ] as const) {
    for (const placeholder of extractPlaceholders(part)) {
      if (!declared.has(placeholder)) {
        errors.push(`${partName} 含未声明变量 ${placeholder}（须在 variables 中声明）`)
      }
    }
  }
  return errors
}
