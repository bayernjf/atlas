/**
 * 纯逻辑层：变量类型/作用域、{{路径}} 引用语法（04 §6）。
 *
 * 变量引用语法（04 §6.3）：{{user.name}}、{{order.items[0].price}}、
 * {{global.company_name}}、{{node_3.result.status}}。
 */

export const VARIABLE_TYPES = ['string', 'number', 'boolean', 'object', 'array'] as const
export type VariableType = (typeof VARIABLE_TYPES)[number]

export type VariableScope = 'global' | 'session' | 'environment' | 'secret'

export type GraphVariable = {
  name: string
  type: VariableType
  value: string
  scope: Extract<VariableScope, 'global'>
}

const TEMPLATE_RE = /\{\{\s*([^{}]+?)\s*\}\}/g

/** 提取模板中的全部变量路径（保持出现顺序，不去重）。 */
export function extractRefs(template: string): string[] {
  return Array.from(template.matchAll(TEMPLATE_RE), (match) => match[1])
}

export type VariableLookup = Record<string, unknown>

/** 用查找表替换 {{路径}}；路径缺失时保留原引用（便于在 UI 中暴露未解析变量）。 */
export function interpolate(template: string, lookup: VariableLookup): string {
  return template.replace(TEMPLATE_RE, (whole, path: string) => {
    const value = resolvePath(path, lookup)
    return value === undefined ? whole : String(value)
  })
}

/** 解析 a.b[0].c 形式路径；任一跳不存在即返回 undefined。 */
export function resolvePath(path: string, lookup: VariableLookup): unknown {
  const parts = path.split('.').flatMap((segment) => {
    const [head, ...indices] = segment.split('[')
    return [head, ...indices.map((index) => index.replace(']', ''))].filter(Boolean)
  })
  let current: unknown = lookup
  for (const part of parts) {
    if (current === null || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
    if (current === undefined) return undefined
  }
  return current
}

/** 校验变量名：合法标识符段（字母/下划线开头），供 {{global.name}} 安全引用。 */
export function isValidVariableName(name: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name)
}

/**
 * 画布可用的引用路径清单（04 §6.3/§6.4）：
 * 全局变量 {{global.name}} + 节点输出 {{<nodeId>.<output>}}。
 * W5-W6 仅暴露全局作用域；会话/环境/秘密变量随后端接入补齐。
 */
export function listVariablePaths(
  variables: GraphVariable[],
  nodes: Array<{ id: string; data: { kind: string } }>,
): string[] {
  const globals = variables.map((variable) => `global.${variable.name}`)
  const outputs = nodes.map((node) => `${node.id}.${nodeOutputKey(node.data.kind)}`)
  return [...globals, ...outputs]
}

function nodeOutputKey(kind: string): string {
  switch (kind) {
    case 'trigger':
      return 'context'
    case 'ai_decision':
      return 'decision'
    case 'tool_call':
      return 'result'
    case 'condition':
      return 'branch'
    case 'loop':
      return 'index'
    default:
      return 'output'
  }
}
