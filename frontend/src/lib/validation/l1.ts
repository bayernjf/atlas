/**
 * L1 字段校验（M2 扶正，08 M2 立项条 / 04 §6.5 / 03 `diagnostic`）。
 *
 * M1 的 schema 派生解释器自 lib/schemas/validateConfig.ts 扶正迁入本文件：
 * schema 关键词产 FIELD_* 诊断（RFC 6901 pointer + 稳定 code + M1 中文文案）；
 * M1 标 covered:false 的跨字段/语法手写规则保留手写、同产 layer:'field' 诊断。
 * dev 双跑脚手架与已被 schema 覆盖的手写规则已删除（U36 等价性证明后不再双轨）。
 * 不引 AJV（ADR T16 收口）；L3 权威仍在后端 dsl.py，本文件不产 graph 诊断。
 */

import { parseExpression, validateExpression } from '../conditions'
import type { ConditionBranch, NodeConfig, ParallelBranch } from '../nodeCatalog'
import { schemaRegistry } from '../schemas'
import type { MetaSchema, NodeConfigSchema } from '../schemas/metaSchema'
import type { Diagnostic } from './diagnostics'
import { escapePointerToken } from './diagnostics'

export const MAX_LOOP_ITERATIONS = 100
export const MIN_PARALLEL_BRANCHES = 2
export const MAX_PARALLEL_BRANCHES = 10
export const MIN_WAIT_SECONDS = 1
export const MAX_WAIT_SECONDS = 3600 // docs/54：duration 上限 600→3600
export const MIN_EVENT_WAIT_SECONDS = 1
export const MAX_EVENT_WAIT_SECONDS = 86400 // docs/54：event 超时上限 3600→86400（24h）
export const MAX_EVENT_KEY_LENGTH = 128
export const MAX_JITTER_SECONDS = 300 // docs/54：duration 抖动上限 0-300 秒
export const MAX_EVENT_KEYS = 8 // docs/54：多事件竞速 1-8 个标识
export const MAX_DURATION_EXPRESSION_LENGTH = 200
export const MAX_ABSOLUTE_TIME_LENGTH = 64
export const WAIT_TIMEOUT_POLICIES = ['continue', 'fail'] as const
export type WaitTimeoutPolicy = (typeof WAIT_TIMEOUT_POLICIES)[number]

const EVENT_KEY_PLACEHOLDER_RE = /\{\{.*?\}\}/g
const EVENT_KEY_RE = /^[A-Za-z0-9:_-]{1,128}$/
const EVENT_KEY_STATIC_RE = /^[A-Za-z0-9:_-]*$/

/** 运行时渲染后的 eventKey：1-128 且仅含 [A-Za-z0-9:_-]（docs/47 §2）。 */
export function validEventKey(key: string): boolean {
  return EVENT_KEY_RE.test(key)
}

/** 静态模板校验：剔除 {{...}} 占位后拼接的静态部分只含白名单字符（占位内不检查）。 */
export function eventKeyStaticValid(template: string): boolean {
  return EVENT_KEY_STATIC_RE.test(template.replace(EVENT_KEY_PLACEHOLDER_RE, ''))
}
export const MIN_APPROVAL_TIMEOUT = 10
export const MAX_APPROVAL_TIMEOUT = 3600
export const APPROVAL_TIMEOUT_ACTIONS = ['approve', 'reject'] as const
export type ApprovalTimeoutAction = (typeof APPROVAL_TIMEOUT_ACTIONS)[number]

export const FIELD_CODES = {
  REQUIRED: 'FIELD_REQUIRED',
  TYPE: 'FIELD_TYPE',
  ENUM: 'FIELD_ENUM',
  CONST: 'FIELD_CONST',
  RANGE: 'FIELD_RANGE',
  LENGTH: 'FIELD_LENGTH',
  PATTERN: 'FIELD_PATTERN',
  ITEMS_MIN: 'FIELD_ITEMS_MIN',
  ITEMS_MAX: 'FIELD_ITEMS_MAX',
  ADDITIONAL_PROPERTIES: 'FIELD_ADDITIONAL_PROPERTIES',
  ONEOF: 'FIELD_ONEOF',
  // covered:false 跨字段/语法手写规则的稳定 code（不属 schema 白名单子集）。
  EXPRESSION_SYNTAX: 'FIELD_EXPRESSION_SYNTAX',
  BRANCH_LABEL_DUPLICATE: 'FIELD_BRANCH_LABEL_DUPLICATE',
  BRANCH_TARGET_DUPLICATE: 'FIELD_BRANCH_TARGET_DUPLICATE',
  DEFAULT_TARGET_COLLISION: 'FIELD_DEFAULT_TARGET_COLLISION',
  LOOP_TARGET_COLLISION: 'FIELD_LOOP_TARGET_COLLISION',
  JOIN_TARGET_COLLISION: 'FIELD_JOIN_TARGET_COLLISION',
  INPUT_KEY_EMPTY: 'FIELD_INPUT_KEY_EMPTY',
  INPUT_KEY_DUPLICATE: 'FIELD_INPUT_KEY_DUPLICATE',
  APPROVAL_TARGET_COLLISION: 'FIELD_APPROVAL_TARGET_COLLISION',
} as const

export type FieldCode = (typeof FIELD_CODES)[keyof typeof FIELD_CODES]

type SchemaFinding = {
  /** RFC 6901 JSON Pointer，相对节点 config 根。 */
  pointer: string
  code: FieldCode
}

const DISCRIMINATOR_CODES = new Set<FieldCode>([FIELD_CODES.TYPE, FIELD_CODES.ENUM, FIELD_CODES.CONST])

function unescapeToken(token: string): string {
  return token.replace(/~1/g, '/').replace(/~0/g, '~')
}

function joinPointer(base: string, key: string | number): string {
  return `${base}/${typeof key === 'number' ? key : escapePointerToken(key)}`
}

/** pointer → 段（已反转义）；''（config 根）→ []。 */
export function pointerSegments(pointer: string): string[] {
  if (!pointer) return []
  return pointer.split('/').slice(1).map(unescapeToken)
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

function validateBranch(schema: MetaSchema, value: unknown, pointer: string, out: SchemaFinding[]): void {
  if (value === undefined || value === null) return

  if (!checkType(schema, value)) {
    out.push({ pointer, code: FIELD_CODES.TYPE })
    return
  }
  if (schema.enum !== undefined && !schema.enum.some((candidate) => candidate === value)) {
    out.push({ pointer, code: FIELD_CODES.ENUM })
  }
  if (schema.const !== undefined && schema.const !== value) {
    out.push({ pointer, code: FIELD_CODES.CONST })
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined) {
      const exclusive = schema.exclusiveMinimum === true
      if ((exclusive ? value <= schema.minimum : value < schema.minimum) ||
        (typeof schema.exclusiveMinimum === 'number' && value <= schema.exclusiveMinimum)) {
        out.push({ pointer, code: FIELD_CODES.RANGE })
      }
    }
    if (schema.maximum !== undefined) {
      const exclusive = schema.exclusiveMaximum === true
      if ((exclusive ? value >= schema.maximum : value > schema.maximum) ||
        (typeof schema.exclusiveMaximum === 'number' && value >= schema.exclusiveMaximum)) {
        out.push({ pointer, code: FIELD_CODES.RANGE })
      }
    }
  }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      out.push({ pointer, code: FIELD_CODES.LENGTH })
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      out.push({ pointer, code: FIELD_CODES.LENGTH })
    }
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
      out.push({ pointer, code: FIELD_CODES.PATTERN })
    }
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      out.push({ pointer, code: FIELD_CODES.ITEMS_MIN })
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      out.push({ pointer, code: FIELD_CODES.ITEMS_MAX })
    }
    if (schema.items) {
      value.forEach((item, index) => validateBranch(schema.items!, item, joinPointer(pointer, index), out))
    }
  }
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const record = value as Record<string, unknown>
    if (Array.isArray(schema.required)) {
      for (const key of schema.required) {
        if (record[key] === undefined) out.push({ pointer: joinPointer(pointer, key), code: FIELD_CODES.REQUIRED })
      }
    }
    if (schema.properties) {
      for (const [key, sub] of Object.entries(schema.properties)) {
        if (key in record) validateBranch(sub, record[key], joinPointer(pointer, key), out)
      }
    }
    if (schema.additionalProperties !== undefined) {
      const declared = new Set(Object.keys(schema.properties ?? {}))
      for (const key of Object.keys(record)) {
        if (declared.has(key)) continue
        if (schema.additionalProperties === false) {
          out.push({ pointer: joinPointer(pointer, key), code: FIELD_CODES.ADDITIONAL_PROPERTIES })
        } else if (typeof schema.additionalProperties === 'object') {
          validateBranch(schema.additionalProperties, record[key], joinPointer(pointer, key), out)
        }
      }
    }
  }
}

function dedupe(findings: SchemaFinding[]): SchemaFinding[] {
  const seen = new Set<string>()
  return findings.filter((finding) => {
    const key = `${finding.pointer}${finding.code}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/**
 * 按白名单子集校验 config；返回 RFC 6901 pointer + FIELD_* code（顺序稳定）。
 * oneOf 沿用 M1 判别分支策略：type/enum/const 全部通过的分支才视为候选，
 * 仅聚合候选分支上的 required/range/pattern 等叶子错误；无候选时聚合判别错误。
 */
export function validateSchemaFields(schema: MetaSchema, config: unknown): SchemaFinding[] {
  const out: SchemaFinding[] = []

  if (Array.isArray(schema.oneOf)) {
    const branchResults = schema.oneOf.map((branch) => {
      const branchErrors: SchemaFinding[] = []
      validateBranch(branch, config, '', branchErrors)
      return dedupe(branchErrors)
    })
    const candidates = branchResults.filter((errors) =>
      errors.every((error) => !DISCRIMINATOR_CODES.has(error.code)),
    )
    if (candidates.length > 0) {
      out.push(...candidates.flat())
    } else {
      out.push(...branchResults.flat())
    }
  }

  const base: SchemaFinding[] = []
  const withoutOneOf = { ...schema, oneOf: undefined }
  validateBranch(withoutOneOf, config, '', base)
  out.push(...base)

  const result = dedupe(out)
  result.sort((a, b) =>
    a.pointer === b.pointer ? a.code.localeCompare(b.code) : a.pointer.localeCompare(b.pointer),
  )
  return result
}

const FALLBACK_MESSAGES: Record<string, string> = {
  [FIELD_CODES.REQUIRED]: '该字段必填',
  [FIELD_CODES.TYPE]: '字段类型不正确',
  [FIELD_CODES.ENUM]: '取值不在允许范围内',
  [FIELD_CODES.CONST]: '取值不被允许',
  [FIELD_CODES.RANGE]: '数值超出允许范围',
  [FIELD_CODES.LENGTH]: '内容长度不符合要求',
  [FIELD_CODES.PATTERN]: '内容格式不符合要求',
  [FIELD_CODES.ITEMS_MIN]: '条目数量不足',
  [FIELD_CODES.ITEMS_MAX]: '条目数量超出上限',
  [FIELD_CODES.ADDITIONAL_PROPERTIES]: '存在不允许的字段',
  [FIELD_CODES.ONEOF]: '未满足任一合法配置组合',
}

function branchTag(config: NodeConfig, index: number): string {
  const branches = (config.branches ?? []) as ConditionBranch[]
  return branches[index]?.label?.trim() || `第 ${index + 1} 个分支`
}

/** schema 派生字段诊断的中文文案（与 M1 手写文案逐条一致；未映射 code 走兜底文案）。 */
function schemaFieldMessage(kind: string, finding: SchemaFinding, config: NodeConfig): string {
  const [head, indexToken, leaf] = pointerSegments(finding.pointer)
  switch (kind) {
    case 'trigger':
      if (finding.pointer === '/cron') return '定时触发必须填写 Cron 表达式'
      if (finding.pointer === '/webhookUrl') return 'Webhook 触发必须填写 URL'
      break
    case 'ai_decision':
      if (finding.pointer === '/promptTemplate') return 'AI 决策必须填写提示词模板'
      if (finding.pointer === '/confidenceThreshold') return '置信度阈值需在 0-1 之间'
      break
    case 'tool_call':
      if (finding.pointer === '/tool') return '工具调用必须选择工具'
      break
    case 'condition':
      if (head === 'branches' && indexToken === undefined) return '条件节点至少需要一个分支'
      if (finding.pointer === '/conditionMode') return '判断模式必须为规则表达式或 LLM 语义判断'
      if (finding.pointer === '/classifierPrompt') return '附加判定要求长度不能超过 500 字符'
      if (head === 'branches') {
        const index = Number(indexToken)
        if (leaf === 'label') return `第 ${index + 1} 个分支名称不能为空`
        if (leaf === 'expression') return `分支 ${branchTag(config, index)} 的表达式不能为空`
        if (leaf === 'description') return `分支 ${branchTag(config, index)} 的语义描述不能为空`
        if (leaf === 'target') return `分支 ${branchTag(config, index)} 必须选择目标节点`
      }
      if (finding.pointer === '/defaultTarget') return '必须配置默认分支'
      break
    case 'loop':
      if (finding.pointer === '/continueExpression') return '必须填写继续条件表达式'
      if (finding.pointer === '/maxIterations') return `最大次数需为 1-${MAX_LOOP_ITERATIONS} 的整数`
      if (finding.pointer === '/itemsExpression') return '必须填写遍历数组表达式'
      if (finding.pointer === '/bodyTarget') return '必须选择循环体入口'
      if (finding.pointer === '/exitTarget') return '必须选择退出目标'
      break
    case 'parallel':
      if (head === 'branches' && indexToken === undefined) {
        return `分支数需为 ${MIN_PARALLEL_BRANCHES}-${MAX_PARALLEL_BRANCHES} 个`
      }
      if (head === 'branches') {
        const index = Number(indexToken)
        if (leaf === 'label') return `第 ${index + 1} 个分支名称不能为空`
        if (leaf === 'target') return `分支 ${branchTag(config, index)} 必须选择目标节点`
      }
      if (finding.pointer === '/joinTarget') return '必须选择汇聚目标'
      break
    case 'wait':
      if (finding.pointer === '/waitType') return '等待类型必须为定时等待或事件等待'
      if (finding.pointer === '/durationSeconds') {
        return `等待时长需为 ${MIN_WAIT_SECONDS}-${MAX_WAIT_SECONDS} 秒的整数`
      }
      if (finding.pointer === '/eventKey') return '事件标识必填（单键 eventKey 或多键 eventKeys 二选一），静态部分仅允许字母、数字及 :_-'
      if (finding.pointer === '/timeoutSeconds') {
        return `超时时间需为 ${MIN_EVENT_WAIT_SECONDS}-${MAX_EVENT_WAIT_SECONDS} 秒的整数`
      }
      if (finding.pointer === '/onTimeout') return '超时策略必须为继续或失败'
      break
    case 'subgraph':
      if (finding.pointer === '/graphId') return '必须选择引用的已保存子图'
      if (head === 'inputs' && indexToken !== undefined) {
        return `入参 ${indexToken || '(空键)'} 的映射值不能为空`
      }
      break
    case 'human_approval':
      if (finding.pointer === '/summary') return '审批说明必填'
      if (finding.pointer === '/timeoutSeconds') {
        return `超时时长需为 ${MIN_APPROVAL_TIMEOUT}-${MAX_APPROVAL_TIMEOUT} 秒的整数`
      }
      if (finding.pointer === '/onTimeout') return '超时策略必须为自动通过或自动拒绝'
      if (finding.pointer === '/approvedTarget') return '必须选择通过目标'
      if (finding.pointer === '/rejectedTarget') return '必须选择拒绝目标'
      break
    default:
      break
  }
  return FALLBACK_MESSAGES[finding.code] ?? '字段校验未通过'
}

function fieldDiag(code: FieldCode, message: string, pointer?: string): Diagnostic {
  return { severity: 'error', layer: 'field', code, message, loc: pointer ? { pointer } : {} }
}

/** M1 标 covered:false 的跨字段/语法手写规则（schema 白名单无法表达，继续手写）。 */
function handFieldDiagnostics(kind: string, config: NodeConfig): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  switch (kind) {
    case 'condition': {
      const branches = (config.branches ?? []) as ConditionBranch[]
      const semantic = config.conditionMode === 'llm'
      const labels = new Set<string>()
      const targets = new Set<string>()
      if (semantic && (config.classifierPrompt?.trim().length ?? 0) > 500) {
        diagnostics.push(
          fieldDiag(FIELD_CODES.LENGTH, '附加判定要求长度不能超过 500 字符', '/classifierPrompt'),
        )
      }
      branches.forEach((branch, index) => {
        const tag = branch.label?.trim() || `第 ${index + 1} 个分支`
        const labelPointer = `/branches/${index}/label`
        const expressionPointer = `/branches/${index}/expression`
        const descriptionPointer = `/branches/${index}/description`
        const targetPointer = `/branches/${index}/target`
        if (branch.label?.trim() && labels.has(branch.label)) {
          diagnostics.push(fieldDiag(FIELD_CODES.BRANCH_LABEL_DUPLICATE, `分支名称重复：${branch.label}`, labelPointer))
        } else if (branch.label?.trim()) {
          labels.add(branch.label)
        }
        if (semantic) {
          if (branch.expression?.trim()) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.EXPRESSION_SYNTAX, `LLM 分支 ${tag} 不允许使用 expression`, expressionPointer),
            )
          }
          const description = branch.description?.trim()
          if (!description) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.REQUIRED, `分支 ${tag} 的语义描述不能为空`, descriptionPointer),
            )
          } else if (description.length > 300) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.LENGTH, `分支 ${tag} 的语义描述长度不能超过 300 字符`, descriptionPointer),
            )
          }
        } else if (branch.expression?.trim()) {
          for (const exprError of validateExpression(branch.expression)) {
            diagnostics.push(fieldDiag(FIELD_CODES.EXPRESSION_SYNTAX, `分支 ${tag} ${exprError}`, expressionPointer))
          }
        }
        if (branch.target?.trim() && targets.has(branch.target)) {
          diagnostics.push(fieldDiag(FIELD_CODES.BRANCH_TARGET_DUPLICATE, `分支目标重复：${branch.target}`, targetPointer))
        } else if (branch.target?.trim()) {
          targets.add(branch.target)
        }
      })
      if (config.defaultTarget?.trim() && targets.has(config.defaultTarget)) {
        diagnostics.push(
          fieldDiag(FIELD_CODES.DEFAULT_TARGET_COLLISION, '默认分支目标不能与其他分支相同', '/defaultTarget'),
        )
      }
      break
    }
    case 'loop': {
      if (config.mode === 'foreach') {
        if (config.itemsExpression?.trim()) {
          const syntaxError = parseExpression(config.itemsExpression)
          if (syntaxError) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.EXPRESSION_SYNTAX, `遍历数组表达式${syntaxError}`, '/itemsExpression'),
            )
          }
        }
        if (config.itemName && !/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(config.itemName)) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.EXPRESSION_SYNTAX, '元素别名须为标识符（字母/下划线开头）', '/itemName'),
          )
        }
      } else if (config.continueExpression?.trim()) {
        for (const exprError of validateExpression(config.continueExpression)) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.EXPRESSION_SYNTAX, `继续条件表达式${exprError}`, '/continueExpression'),
          )
        }
      }
      if (config.bodyTarget && config.bodyTarget === config.exitTarget) {
        diagnostics.push(fieldDiag(FIELD_CODES.LOOP_TARGET_COLLISION, '循环体入口与退出目标不能相同', '/bodyTarget'))
      }
      break
    }
    case 'parallel': {
      const branches = (config.branches ?? []) as ParallelBranch[]
      const labels = new Set<string>()
      const targets = new Set<string>()
      branches.forEach((branch, index) => {
        const labelPointer = `/branches/${index}/label`
        const targetPointer = `/branches/${index}/target`
        if (branch.label?.trim() && labels.has(branch.label)) {
          diagnostics.push(fieldDiag(FIELD_CODES.BRANCH_LABEL_DUPLICATE, `分支名称重复：${branch.label}`, labelPointer))
        } else if (branch.label?.trim()) {
          labels.add(branch.label)
        }
        if (branch.target?.trim() && targets.has(branch.target)) {
          diagnostics.push(fieldDiag(FIELD_CODES.BRANCH_TARGET_DUPLICATE, `分支目标重复：${branch.target}`, targetPointer))
        } else if (branch.target?.trim()) {
          targets.add(branch.target)
        }
      })
      if (config.joinTarget?.trim() && targets.has(config.joinTarget)) {
        diagnostics.push(
          fieldDiag(FIELD_CODES.JOIN_TARGET_COLLISION, '汇聚目标不能与任一分支目标相同', '/joinTarget'),
        )
      }
      break
    }
    case 'wait': {
      if (config.waitType === 'event') {
        const eventKeys = Array.isArray(config.eventKeys) ? config.eventKeys : undefined
        if (eventKeys) {
          // docs/54：eventKey 与 eventKeys 互斥
          const single = config.eventKey ?? ''
          if (single.trim()) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.PATTERN, 'eventKey 与 eventKeys 互斥，请只保留一种', '/eventKeys'),
            )
          }
          if (eventKeys.length < 1 || eventKeys.length > MAX_EVENT_KEYS) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.LENGTH, `多事件需 1-${MAX_EVENT_KEYS} 个事件标识`, '/eventKeys'),
            )
          }
          const seen = new Set<string>()
          eventKeys.forEach((raw, idx) => {
            const key = String(raw ?? '')
            const pointer = `/eventKeys/${idx}`
            if (!key.trim()) {
              diagnostics.push(fieldDiag(FIELD_CODES.REQUIRED, `第 ${idx + 1} 个事件标识必填`, pointer))
            } else if (key.length > MAX_EVENT_KEY_LENGTH || !eventKeyStaticValid(key)) {
              diagnostics.push(
                fieldDiag(FIELD_CODES.PATTERN, `第 ${idx + 1} 个事件标识静态部分仅允许字母、数字及 :_-`, pointer),
              )
            } else if (seen.has(key.trim())) {
              diagnostics.push(fieldDiag(FIELD_CODES.INPUT_KEY_DUPLICATE, `多事件标识重复：${key.trim()}`, pointer))
            } else {
              seen.add(key.trim())
            }
          })
          // docs/55：eventWaitMode=all（AND 竞速）需至少 2 个事件
          if (config.eventWaitMode === 'all' && eventKeys.length < 2) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.LENGTH, '全部命中（eventWaitMode=all）需配置至少 2 个事件', '/eventWaitMode'),
            )
          }
        } else if (config.eventWaitMode === 'all') {
          // 单键 eventKey 不支持 AND
          diagnostics.push(
            fieldDiag(FIELD_CODES.PATTERN, '全部命中（eventWaitMode=all）需改用多事件 eventKeys 且至少 2 个', '/eventWaitMode'),
          )
        } else {
          const template = config.eventKey ?? ''
          if (!template.trim()) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.REQUIRED, '事件标识必填（单键 eventKey 或多键 eventKeys 二选一）', '/eventKey'),
            )
          } else if (!eventKeyStaticValid(template)) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.PATTERN, '事件标识静态部分仅允许字母、数字及 :_-，占位内不检查', '/eventKey'),
            )
          }
        }
        if (config.timeoutMode === 'expression') {
          const timeoutExpression = config.timeoutExpression ?? ''
          if (!timeoutExpression.trim()) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.REQUIRED, '超时表达式为必填', '/timeoutExpression'),
            )
          } else if (timeoutExpression.length > MAX_DURATION_EXPRESSION_LENGTH) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.LENGTH, `超时表达式长度不能超过 ${MAX_DURATION_EXPRESSION_LENGTH} 字符`, '/timeoutExpression'),
            )
          }
        } else {
          const eventTimeout = config.timeoutSeconds
          if (eventTimeout === undefined) {
            diagnostics.push(
              fieldDiag(FIELD_CODES.REQUIRED, `超时时间为必填（1-${MAX_EVENT_WAIT_SECONDS} 秒整数）`, '/timeoutSeconds'),
            )
          }
        }
      } else if (config.durationMode === 'dynamic') {
        const expression = config.durationExpression ?? ''
        if (!expression.trim()) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.REQUIRED, '动态时长表达式为必填', '/durationExpression'),
          )
        } else if (expression.length > MAX_DURATION_EXPRESSION_LENGTH) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.LENGTH, `动态时长表达式长度不能超过 ${MAX_DURATION_EXPRESSION_LENGTH} 字符`, '/durationExpression'),
          )
        }
      } else if (config.durationMode === 'absolute') {
        const absoluteTime = config.absoluteTime ?? ''
        if (!absoluteTime.trim()) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.REQUIRED, '到点时刻为必填', '/absoluteTime'),
          )
        } else if (absoluteTime.trim().length > MAX_ABSOLUTE_TIME_LENGTH) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.LENGTH, `到点时刻长度不能超过 ${MAX_ABSOLUTE_TIME_LENGTH} 字符`, '/absoluteTime'),
          )
        }
      } else {
        const seconds = config.durationSeconds
        if (seconds === undefined) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.REQUIRED, `等待时长为必填（1-${MAX_WAIT_SECONDS} 秒整数）`, '/durationSeconds'),
          )
        }
        // docs/54：仅 static 固定时长支持 jitterSeconds（0-300 整数，缺省 0）
        if (
          config.jitterSeconds !== undefined &&
          (!Number.isInteger(config.jitterSeconds) ||
            config.jitterSeconds < 0 ||
            config.jitterSeconds > MAX_JITTER_SECONDS)
        ) {
          diagnostics.push(
            fieldDiag(FIELD_CODES.RANGE, `抖动上限需为 0-${MAX_JITTER_SECONDS} 秒的整数`, '/jitterSeconds'),
          )
        }
      }
      break
    }
    case 'subgraph': {
      const keys = new Set<string>()
      for (const key of Object.keys(config.inputs ?? {})) {
        if (!key.trim()) {
          diagnostics.push(fieldDiag(FIELD_CODES.INPUT_KEY_EMPTY, '入参键名不能为空', '/inputs'))
        } else if (keys.has(key)) {
          diagnostics.push(fieldDiag(FIELD_CODES.INPUT_KEY_DUPLICATE, `入参键名重复：${key}`, `/inputs/${escapePointerToken(key)}`))
        } else {
          keys.add(key)
        }
      }
      break
    }
    case 'human_approval': {
      if (config.approvedTarget && config.approvedTarget === config.rejectedTarget) {
        diagnostics.push(
          fieldDiag(FIELD_CODES.APPROVAL_TARGET_COLLISION, '通过目标与拒绝目标不能相同', '/approvedTarget'),
        )
      }
      break
    }
    default:
      break
  }
  return diagnostics
}

/**
 * 单节点 config 的全部 L1 field 诊断（schema 派生 + 手写跨字段）。
 * loc.nodeId 由调用方（validateGraph）按所属节点补；节点名称必填不属 config，
 * 同样由调用方补（无 pointer、仅 nodeId）。
 */
export function validateNodeFields(kind: string, config: NodeConfig): Diagnostic[] {
  let schema: NodeConfigSchema
  try {
    schema = schemaRegistry.get(kind)
  } catch {
    return []
  }
  const schemaDiagnostics = validateSchemaFields(schema, config).map((finding) =>
    fieldDiag(finding.code, schemaFieldMessage(kind, finding, config), finding.pointer),
  )
  return [...schemaDiagnostics, ...handFieldDiagnostics(kind, config)]
}

/**
 * 工具 input_schema 的字段诊断（M3，08 M3 立项条④ / 04 §4.10 校验条）。
 *
 * pointer 相对 params 对象根（`/sql`、`/headers/X`），供 FormRenderer 命中字段；
 * 工具 schema 没有节点种类文案表，中文统一走 FIELD_* 兜底文案。仅设计态提示：
 * 保存/编译/运行的权威仍是后端 L3，本函数不参与 validateGraph 聚合。
 */
export function validateParamFields(schema: MetaSchema, params: unknown): Diagnostic[] {
  return validateSchemaFields(schema, params).map((finding) =>
    fieldDiag(finding.code, FALLBACK_MESSAGES[finding.code] ?? '字段校验未通过', finding.pointer),
  )
}
