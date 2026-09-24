/**
 * 运行期错误码 → 当前语言文案解析（docs/60 G1）。
 *
 * 后端在 run failed 通道（同步 500 的 `detail:{code,message,params}`、SSE `event: error`
 * 帧）与 loop/foreach 节点结果（`expressionErrorCodes`，与 `expression_errors` 等长）
 * 并行下发机器可读码：COND_*（表达式求值）、WAIT_*（等待节点）、LOOP_* 与 FOREACH_*
 * （循环）、RUNTIME_UNEXPECTED（兜底）。中文 `message`、`expression_errors` 始终保留，
 * 是缺翻译键时的兜底真相——本模块保证英文态最坏只回退中文、绝不泄漏 i18n key。
 *
 * 与 lib/apiClient.ts 内的 422 校验列表解析（resolveValidationList，validation.dsl.*）
 * 相互独立：那是编译期 DSL 诊断，本模块面向运行期终态/节点错误（runtime.* 命名空间）。
 */
import { getLanguage, t } from '../locales'

/** 运行期错误码前缀（与后端 graph/conditions.py、graph/loader.py 码族对齐）。 */
const RUNTIME_CODE_RE = /^(COND_|WAIT_|LOOP_|FOREACH_|RUNTIME_)/

const isEn = () => getLanguage().startsWith('en')

/** params.actual / params.expected 使用的英文类型码 → 双语标签。 */
const TYPE_LABELS: Record<string, { zh: string; en: string }> = {
  null: { zh: '空值', en: 'null' },
  boolean: { zh: '布尔值', en: 'boolean' },
  number: { zh: '数值', en: 'number' },
  integer: { zh: '整数', en: 'integer' },
  string: { zh: '字符串', en: 'string' },
  date: { zh: '日期', en: 'date' },
  datetime: { zh: '日期时间', en: 'datetime' },
  array: { zh: '数组', en: 'array' },
  object: { zh: '对象', en: 'object' },
}

/** date(y,m,d)/datetime(...) 分量 token → 双语标签。 */
const COMPONENT_LABELS: Record<string, { zh: string; en: string }> = {
  year: { zh: '年份', en: 'year' },
  month: { zh: '月份', en: 'month' },
  day: { zh: '日份', en: 'day' },
  hour: { zh: '小时', en: 'hour' },
  minute: { zh: '分钟', en: 'minute' },
  second: { zh: '秒', en: 'second' },
}

function labelType(code: unknown): string {
  if (typeof code !== 'string' || code === '') return ''
  // 组合期望类型（如 number|string|date、string|array|object）按分隔符拆开各自映射。
  return code
    .split('|')
    .map((part) => {
      const hit = TYPE_LABELS[part]
      if (hit) return isEn() ? hit.en : hit.zh
      return part
    })
    .join(isEn() ? ' | ' : '/')
}

function labelComponent(token: unknown): string {
  if (typeof token !== 'string') return ''
  const hit = COMPONENT_LABELS[token]
  return hit ? (isEn() ? hit.en : hit.zh) : token
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map((v) => String(v)).join(isEn() ? ', ' : '、')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/**
 * COND_TYPE_MISMATCH 的 params 形态较多（运算符 op 或函数 func、期望/实际类型、date
 * 分量 component），catalog 只暴露 {{detail}}，由这里按当前语言组装完整短语，保证
 * zh/en 模板占位符集合一致。
 */
function buildTypeMismatchDetail(params: Record<string, unknown>): string {
  const expected = labelType(params.expected)
  const actual = labelType(params.actual)
  const op = typeof params.op === 'string' ? params.op : ''
  const func = typeof params.func === 'string' ? params.func : ''
  const component = labelComponent(params.component)

  if (component) {
    // date/datetime 分量必须是整数（params 无 actual，只有 component）。
    if (isEn()) {
      const fn = func || 'date'
      return `the ${component} argument of ${fn}() must be ${expected || 'an integer'}`
    }
    return `函数“${func || 'date'}”的${component}必须是${expected || '整数'}`
  }

  const subject = op
    ? isEn()
      ? `operator '${op}'`
      : `运算符“${op}”`
    : func
      ? isEn()
        ? `function ${func}()`
        : `函数“${func}”`
      : ''
  if (isEn()) {
    const head = subject ? `${subject} expects ${expected}` : `expected ${expected}`
    return actual ? `${head}, got ${actual}` : head
  }
  const head = subject ? `${subject}要求${expected}` : `要求${expected}`
  return actual ? `${head}，实际为 ${actual}` : head
}

/** 把后端 params 归一化为可插值的字符串变量（类型/分量码按当前语言本地化）。 */
function localizeParams(
  code: string,
  params: Record<string, unknown> | undefined,
): Record<string, string> {
  const out: Record<string, string> = {}
  const source = params ?? {}
  for (const [key, value] of Object.entries(source)) {
    if (key === 'expected') out.expected = labelType(value)
    else if (key === 'actual') out.actual = labelType(value)
    else if (key === 'component') out.component = labelComponent(value)
    else out[key] = stringify(value)
  }
  // COND_TYPE_MISMATCH 模板只取 {{detail}}，由运算符/函数/分量等 params 组装。
  if (code === 'COND_TYPE_MISMATCH') out.detail = buildTypeMismatchDetail(source)
  return out
}

export function isRuntimeErrorCode(code: unknown): code is string {
  return typeof code === 'string' && RUNTIME_CODE_RE.test(code)
}

/**
 * 把运行期错误码解析为当前语言文案；无码或无翻译键时回退 fallback（后端中文 message），
 * 保证不泄漏 i18n key。
 */
export function resolveRuntimeError(
  code: string | undefined,
  params: Record<string, unknown> | undefined,
  fallback: string,
): string {
  if (!isRuntimeErrorCode(code)) return fallback
  return t(code, {
    ns: 'runtime',
    defaultValue: fallback,
    ...localizeParams(code, params),
  })
}

/**
 * 解析 FastAPI 运行失败 detail：可能是字符串（旧形状）或
 * `{code,message,params}` / `{code,message,nodeId}` 对象（docs/60 G1）。
 */
export function resolveRuntimeDetail(detail: unknown): string {
  if (detail !== null && typeof detail === 'object') {
    const rec = detail as { code?: string; message?: string; params?: Record<string, unknown> }
    const fallback = rec.message ?? ''
    return resolveRuntimeError(rec.code, rec.params, fallback)
  }
  return typeof detail === 'string' ? detail : ''
}

/**
 * 把节点结果中与 expression_errors 等长的 expressionErrorCodes 逐条解析为当前语言明细；
 * 无码或码不可识别的条目回退对应中文 expression_errors（fail-safe，不泄漏 key）。
 */
export function resolveExpressionErrors(
  codes: unknown,
  messages: unknown,
): string[] {
  const msgs = Array.isArray(messages) ? messages.map((m) => String(m)) : []
  if (!Array.isArray(codes)) return msgs
  return codes.map((code, index) => {
    const fallback = msgs[index] ?? ''
    if (!isRuntimeErrorCode(code)) return fallback
    const resolved = resolveRuntimeError(code, undefined, fallback)
    // 节点结果码通道不携带 params：模板若残留未插值占位，回退该条后端中文明细，
    // 绝不把 {{op}} 之类的原始占位上屏。
    return resolved.includes('{{') || resolved.includes('}}') ? fallback : resolved
  })
}
