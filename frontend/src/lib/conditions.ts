/**
 * condition/loop 安全规则表达式（04 §5.1 语法白名单 / §5.2 契约；D15 扩算术与函数）。
 *
 * 与后端 atlas.graph.conditions 同构的手写递归下降解析，禁止 eval、零依赖。
 * 前端只做语法 + 静态类型校验（实时中文报错，含无变量子树的常量折叠）；
 * 真正求值在后端进行，含变量的运行时类型错误由后端 fail-safe 走 defaultTarget。
 * 白名单函数（无副作用；today()/now() 为非确定函数，值由后端可注入时钟决定，
 * 录制/回放冻结；前端静态校验对二者跳过常量折叠，类型按函数表推断）：
 *   数值 abs/floor/ceil/round/min/max；字符串 len/lower/upper；
 *   日期 date/year/month/day/daysBetween/today；
 *   日期时间 now/datetime/hoursBetween（date 与 datetime 不可跨类型有序比较）。
 */

const TOKEN_RE =
  /\{\{\s*[^{}]+?\s*\}\}|>=|<=|==|!=|&&|\|\||[()!><+\-*/%,]|\d+(?:\.\d+)?|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*/gy

const CMP_OPS = new Set(['>', '>=', '<', '<=', '==', '!='])
const ADD_OPS = new Set(['+', '-'])
const MUL_OPS = new Set(['*', '/', '%'])

type Token = { kind: string; value: string; pos: number }
type DateValue = { readonly __date: true; readonly y: number; readonly m: number; readonly d: number }
type DateTimeValue = {
  readonly __datetime: true
  readonly y: number; readonly m: number; readonly d: number
  readonly H: number; readonly M: number; readonly S: number
}
type AstNode =
  | ['lit', unknown]
  | ['var', string]
  | ['unary', '!' | '-' | '+', AstNode]
  | ['binary', string, AstNode, AstNode]
  | ['call', string, AstNode[]]

// 白名单函数：name -> [最少参数, 最多参数(null=不限), 返回类型]
const FUNCTIONS: Record<string, [number, number | null, string]> = {
  abs: [1, 1, 'number'],
  floor: [1, 1, 'number'],
  ceil: [1, 1, 'number'],
  round: [1, 1, 'number'],
  min: [1, null, 'number'],
  max: [1, null, 'number'],
  len: [1, 1, 'number'],
  lower: [1, 1, 'string'],
  upper: [1, 1, 'string'],
  date: [3, 3, 'date'],
  year: [1, 1, 'number'],
  month: [1, 1, 'number'],
  day: [1, 1, 'number'],
  daysBetween: [2, 2, 'number'],
  // 非确定日期/时间（后端取注入时钟）与 UTC datetime 体系（D15 余部，docs/27 C）。
  today: [0, 0, 'date'],
  now: [0, 0, 'datetime'],
  datetime: [5, 6, 'datetime'],
  hoursBetween: [2, 2, 'number'],
}
const FN_NAMES = Object.keys(FUNCTIONS).join(', ')
// 非确定函数：静态校验期不做常量折叠（其值依赖运行时钟）。
const NONDETERMINISTIC = new Set(['today', 'now'])

function tokenize(expression: string): Token[] {
  const tokens: Token[] = []
  TOKEN_RE.lastIndex = 0
  let pos = 0
  while (pos < expression.length) {
    if (/\s/.test(expression[pos])) {
      pos += 1
      continue
    }
    TOKEN_RE.lastIndex = pos
    const match = TOKEN_RE.exec(expression)
    if (!match || match.index !== pos) {
      throw new Error(`语法错误：意外字符 "${expression[pos]}"（位置 ${pos + 1}）`)
    }
    const text = match[0]
    if (text.startsWith('{{')) tokens.push({ kind: 'path', value: text.slice(2, -2).trim(), pos })
    else if (/^\d/.test(text)) tokens.push({ kind: 'number', value: text, pos })
    else if (text[0] === "'" || text[0] === '"') tokens.push({ kind: 'string', value: text.slice(1, -1), pos })
    else if (text === 'true' || text === 'false' || text === 'null') {
      tokens.push({ kind: 'literal', value: text, pos })
    } else if (
      CMP_OPS.has(text) ||
      ADD_OPS.has(text) ||
      MUL_OPS.has(text) ||
      text === '&&' ||
      text === '||' ||
      text === '!' ||
      text === '(' ||
      text === ')' ||
      text === ','
    ) {
      tokens.push({ kind: 'op', value: text, pos })
    } else {
      // 裸标识符：函数名由 parser 结合其后的 '(' 判定，其余为未知标识符。
      tokens.push({ kind: 'ident', value: text, pos })
    }
    pos = match.index + text.length
  }
  return tokens
}

class Parser {
  private index = 0
  private readonly tokens: Token[]

  constructor(tokens: Token[]) {
    this.tokens = tokens
  }

  private peek(): Token | undefined {
    return this.tokens[this.index]
  }

  private consume(): Token {
    const token = this.peek()
    if (!token) throw new Error('语法错误：表达式不完整')
    this.index += 1
    return token
  }

  parse(): AstNode {
    const node = this.parseOr()
    if (this.peek()) {
      const token = this.peek() as Token
      throw new Error(`语法错误：意外的 token "${token.value}"（位置 ${token.pos + 1}）`)
    }
    return node
  }

  private parseOr(): AstNode {
    let node = this.parseAnd()
    while (this.peek()?.value === '||') {
      this.consume()
      node = ['binary', '||', node, this.parseAnd()]
    }
    return node
  }

  private parseAnd(): AstNode {
    let node = this.parseNot()
    while (this.peek()?.value === '&&') {
      this.consume()
      node = ['binary', '&&', node, this.parseNot()]
    }
    return node
  }

  private parseNot(): AstNode {
    if (this.peek()?.value === '!') {
      this.consume()
      return ['unary', '!', this.parseNot()]
    }
    return this.parseComparison()
  }

  private parseComparison(): AstNode {
    const left = this.parseAdditive()
    const token = this.peek()
    if (token && CMP_OPS.has(token.value)) {
      this.consume()
      return ['binary', token.value, left, this.parseAdditive()]
    }
    return left
  }

  private parseAdditive(): AstNode {
    let node = this.parseMultiplicative()
    let token = this.peek()
    while (token && ADD_OPS.has(token.value)) {
      this.consume()
      node = ['binary', token.value, node, this.parseMultiplicative()]
      token = this.peek()
    }
    return node
  }

  private parseMultiplicative(): AstNode {
    let node = this.parseUnary()
    let token = this.peek()
    while (token && MUL_OPS.has(token.value)) {
      this.consume()
      node = ['binary', token.value, node, this.parseUnary()]
      token = this.peek()
    }
    return node
  }

  private parseUnary(): AstNode {
    const token = this.peek()
    if (token && (token.value === '-' || token.value === '+')) {
      this.consume()
      return ['unary', token.value, this.parseUnary()]
    }
    return this.parsePrimary()
  }

  private parsePrimary(): AstNode {
    const token = this.consume()
    if (token.value === '(') {
      const node = this.parseOr()
      const closing = this.peek()
      if (!closing || closing.value !== ')') {
        throw new Error(`语法错误：缺少右括号（自位置 ${token.pos + 1}）`)
      }
      this.consume()
      return node
    }
    if (token.kind === 'path') return ['var', token.value]
    if (token.kind === 'number') {
      return ['lit', token.value.includes('.') ? Number.parseFloat(token.value) : Number.parseInt(token.value, 10)]
    }
    if (token.kind === 'string') return ['lit', token.value]
    if (token.kind === 'literal') {
      return ['lit', token.value === 'true' ? true : token.value === 'false' ? false : null]
    }
    if (token.kind === 'ident') return this.parseIdentifier(token)
    throw new Error(`语法错误：意外的 token "${token.value}"（位置 ${token.pos + 1}）`)
  }

  private parseIdentifier(token: Token): AstNode {
    const next = this.peek()
    if (!next || next.value !== '(') {
      throw new Error(
        `语法错误：未知标识符 "${token.value}"（位置 ${token.pos + 1}；变量须用 {{路径}} 包裹，` +
          `字面量仅支持 true/false/null、数字、字符串，函数仅限白名单：${FN_NAMES}）`,
      )
    }
    this.consume() // '('
    const args: AstNode[] = []
    if (this.peek() && this.peek()?.value !== ')') {
      args.push(this.parseOr())
      while (this.peek()?.value === ',') {
        this.consume()
        args.push(this.parseOr())
      }
    }
    const closing = this.peek()
    if (!closing || closing.value !== ')') {
      throw new Error(`语法错误：函数 "${token.value}" 缺少右括号`)
    }
    this.consume() // ')'
    if (!(token.value in FUNCTIONS)) {
      throw new Error(`语法错误：未知函数 "${token.value}"（仅支持白名单函数：${FN_NAMES}）`)
    }
    const [minArgs, maxArgs] = FUNCTIONS[token.value]
    if (args.length < minArgs || (maxArgs !== null && args.length > maxArgs)) {
      const want =
        minArgs === maxArgs ? `${minArgs} 个参数` : maxArgs === null ? `至少 ${minArgs} 个参数` : `${minArgs}-${maxArgs} 个参数`
      throw new Error(`函数 "${token.value}" 需要${want}，实际 ${args.length} 个`)
    }
    return ['call', token.value, args]
  }
}

function isDate(value: unknown): value is DateValue {
  return !!value && typeof value === 'object' && (value as DateValue).__date === true
}

function isDateTime(value: unknown): value is DateTimeValue {
  return !!value && typeof value === 'object' && (value as DateTimeValue).__datetime === true
}

function isTemporal(value: unknown): value is DateValue | DateTimeValue {
  return isDate(value) || isDateTime(value)
}

function temporalKind(value: unknown): 'date' | 'datetime' | null {
  if (isDateTime(value)) return 'datetime'
  if (isDate(value)) return 'date'
  return null
}

function isNum(value: unknown): value is number {
  return typeof value === 'number'
}

function typeName(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return '布尔'
  if (typeof value === 'number') return '数字'
  if (typeof value === 'string') return '字符串'
  if (isDateTime(value)) return '日期时间'
  if (isDate(value)) return '日期'
  return typeof value
}

function isLeap(y: number): boolean {
  return y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0)
}

function makeDate(y: number, m: number, d: number): DateValue {
  if (![y, m, d].every(Number.isInteger)) {
    throw new Error('函数 "date" 的年/月/日份必须是整数')
  }
  const dim = [31, isLeap(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  if (m < 1 || m > 12 || d < 1 || d > dim[m - 1]) {
    throw new Error(`函数 "date" 构造了非法日期：${y}-${m}-${d}`)
  }
  return { __date: true, y, m, d }
}

function makeDateTime(
  y: number, m: number, d: number, H: number, M: number, S: number,
): DateTimeValue {
  if (![y, m, d, H, M, S].every(Number.isInteger)) {
    throw new Error('函数 "datetime" 的年/月/日/小时/分钟/秒必须是整数')
  }
  makeDate(y, m, d) // 复用日期（含闰年）校验，非法即抛
  if (H < 0 || H > 23 || M < 0 || M > 59 || S < 0 || S > 59) {
    throw new Error(`函数 "datetime" 构造了非法日期时间：${y}-${m}-${d} ${H}:${M}:${S}`)
  }
  return { __datetime: true, y, m, d, H, M, S }
}

// 日期时间换算为自序数原点的秒数（date 按当日 00:00），供小时差与 datetime 有序比较。
function temporalTicks(value: DateValue | DateTimeValue): number {
  const daySeconds = toOrdinal(value) * 86400
  return isDateTime(value) ? daySeconds + value.H * 3600 + value.M * 60 + value.S : daySeconds
}

// 与 Python date.toordinal 同构（Howard Hinnant 公式），供天数差与日期有序比较。
function toOrdinal(date: { y: number; m: number; d: number }): number {
  const a = Math.trunc((14 - date.m) / 12)
  const yy = date.y + 4800 - a
  const mm = date.m + 12 * a - 3
  return (
    date.d +
    Math.trunc((153 * mm + 2) / 5) +
    365 * yy +
    Math.trunc(yy / 4) -
    Math.trunc(yy / 100) +
    Math.trunc(yy / 400) -
    32045
  )
}

function evalFunction(name: string, args: unknown[]): unknown {
  const requireNumber = (value: unknown): void => {
    if (!isNum(value)) throw new Error(`函数 "${name}" 要求数值参数，实际为 ${typeName(value)}`)
  }
  switch (name) {
    case 'abs':
      requireNumber(args[0])
      return Math.abs(args[0] as number)
    case 'floor':
      requireNumber(args[0])
      return Math.floor(args[0] as number)
    case 'ceil':
      requireNumber(args[0])
      return Math.ceil(args[0] as number)
    case 'round':
      requireNumber(args[0])
      return Math.floor((args[0] as number) + 0.5) // 半值向正无穷，与后端同构
    case 'min':
    case 'max':
      args.forEach(requireNumber)
      return name === 'min' ? Math.min(...(args as number[])) : Math.max(...(args as number[]))
    case 'len': {
      const value = args[0]
      if (typeof value === 'string' || Array.isArray(value)) return value.length
      if (value && typeof value === 'object') return Object.keys(value).length
      throw new Error(`函数 "len" 要求字符串/数组/对象，实际为 ${typeName(value)}`)
    }
    case 'lower':
    case 'upper':
      if (typeof args[0] !== 'string') {
        throw new Error(`函数 "${name}" 要求字符串，实际为 ${typeName(args[0])}`)
      }
      return name === 'lower' ? args[0].toLowerCase() : args[0].toUpperCase()
    case 'date':
      return makeDate(args[0] as number, args[1] as number, args[2] as number)
    case 'year':
    case 'month':
    case 'day':
      if (!isTemporal(args[0])) {
        throw new Error(`函数 "${name}" 要求日期值（用 date(y,m,d) 构造），实际为 ${typeName(args[0])}`)
      }
      return args[0][name === 'year' ? 'y' : name === 'month' ? 'm' : 'd']
    case 'daysBetween': {
      const [start, end] = args
      if (!isTemporal(start) || !isTemporal(end)) {
        throw new Error(`函数 "daysBetween" 要求两个日期值，实际为 ${typeName(start)} 与 ${typeName(end)}`)
      }
      return toOrdinal(end) - toOrdinal(start)
    }
    case 'today': {
      const n = new Date()
      return makeDate(n.getUTCFullYear(), n.getUTCMonth() + 1, n.getUTCDate())
    }
    case 'now': {
      const n = new Date()
      return makeDateTime(
        n.getUTCFullYear(), n.getUTCMonth() + 1, n.getUTCDate(),
        n.getUTCHours(), n.getUTCMinutes(), n.getUTCSeconds(),
      )
    }
    case 'datetime':
      return makeDateTime(
        args[0] as number, args[1] as number, args[2] as number,
        args[3] as number, args[4] as number, (args[5] ?? 0) as number,
      )
    case 'hoursBetween': {
      const [start, end] = args
      if (!isTemporal(start) || !isTemporal(end)) {
        throw new Error(
          `函数 "hoursBetween" 要求日期时间值（用 datetime(...) 或 now() 构造，日期按当日 00:00 UTC），实际为 ${typeName(start)}`,
        )
      }
      return (temporalTicks(end as DateValue | DateTimeValue) - temporalTicks(start as DateValue | DateTimeValue)) / 3600
    }
    default:
      throw new Error(`未知函数 "${name}"`)
  }
}

function arith(op: string, left: unknown, right: unknown): unknown {
  if (!isNum(left) || !isNum(right) || typeof left === 'boolean' || typeof right === 'boolean') {
    throw new Error(`算术 "${op}" 要求两侧均为数值，实际为 ${typeName(left)} 与 ${typeName(right)}`)
  }
  switch (op) {
    case '+':
      return left + right
    case '-':
      return left - right
    case '*':
      return left * right
    case '/':
      if (right === 0) throw new Error('算术 "/" 除数不能为 0')
      return left / right
    case '%':
      if (right === 0) throw new Error('算术 "%" 模数不能为 0')
      return left - right * Math.trunc(left / right) // 截断式余数，与后端同构
    default:
      throw new Error(`未知算术符 "${op}"`)
  }
}

function compare(op: string, left: unknown, right: unknown): boolean {
  if (op === '==' || op === '!=') {
    let equal: boolean
    if (isDateTime(left) && isDateTime(right)) {
      equal =
        left.y === right.y && left.m === right.m && left.d === right.d &&
        left.H === right.H && left.M === right.M && left.S === right.S
    } else if (isDate(left) && isDate(right)) {
      equal = left.y === right.y && left.m === right.m && left.d === right.d
    } else {
      // date 与 datetime 跨类型走严格相等（不同对象 → false），与后端一致
      equal = left === right
    }
    return op === '==' ? equal : !equal
  }
  if (left === null || right === null) {
    throw new Error('空值（null/缺失变量）只能做 == / != 比较，不能参与大小比较')
  }
  if (
    typeof left === 'boolean' ||
    typeof right === 'boolean' ||
    typeof left !== typeof right ||
    temporalKind(left) !== temporalKind(right)
  ) {
    throw new Error(`有序比较 "${op}" 要求两侧同为数字、字符串、日期或日期时间，实际为 ${typeName(left)} 与 ${typeName(right)}`)
  }
  if (!(typeof left === 'number' || typeof left === 'string' || isTemporal(left))) {
    throw new Error(`有序比较 "${op}" 不支持类型 ${typeName(left)}`)
  }
  const lv = isDateTime(left)
    ? temporalTicks(left)
    : isDate(left)
      ? toOrdinal(left)
      : (left as number | string)
  const rv = isDateTime(right)
    ? temporalTicks(right)
    : isDate(right)
      ? toOrdinal(right)
      : (right as number | string)
  return op === '>' ? lv > rv : op === '>=' ? lv >= rv : op === '<' ? lv < rv : lv <= rv
}

/** 仅对无变量 AST 做常量求值，供静态折叠暴露除零/类型/非法日期等错误。 */
function evaluateConst(node: AstNode): unknown {
  switch (node[0]) {
    case 'lit':
      return node[1]
    case 'var':
      throw new Error('内部错误：含变量子树不应进入常量求值')
    case 'unary': {
      const value = evaluateConst(node[2])
      if (node[1] === '!') {
        if (typeof value !== 'boolean') throw new Error(`逻辑非 "!" 要求布尔值，实际为 ${typeName(value)}`)
        return !value
      }
      if (!isNum(value) || typeof value === 'boolean') {
        throw new Error(`一元 "${node[1]}" 要求数值，实际为 ${typeName(value)}`)
      }
      return node[1] === '+' ? +value : -value
    }
    case 'call':
      return evalFunction(node[1], node[2].map(evaluateConst))
    case 'binary': {
      const [, op, leftNode, rightNode] = node
      if (op === '&&') {
        const left = evaluateConst(leftNode)
        if (typeof left !== 'boolean') throw new Error(`"&&" 要求布尔值，实际为 ${typeName(left)}`)
        return left && evaluateConst(rightNode)
      }
      if (op === '||') {
        const left = evaluateConst(leftNode)
        if (typeof left !== 'boolean') throw new Error(`"||" 要求布尔值，实际为 ${typeName(left)}`)
        return left || evaluateConst(rightNode)
      }
      if (CMP_OPS.has(op)) return compare(op, evaluateConst(leftNode), evaluateConst(rightNode))
      return arith(op, evaluateConst(leftNode), evaluateConst(rightNode))
    }
  }
}

function hasVar(node: AstNode): boolean {
  if (node[0] === 'var') return true
  if (node[0] === 'lit') return false
  if (node[0] === 'unary') return hasVar(node[2])
  if (node[0] === 'call') return node[2].some(hasVar)
  return hasVar(node[2]) || hasVar(node[3])
}

function inferType(node: AstNode): string {
  switch (node[0]) {
    case 'var':
      return 'unknown'
    case 'lit': {
      const value = node[1]
      if (typeof value === 'boolean') return 'bool'
      if (typeof value === 'number') return 'number'
      if (typeof value === 'string') return 'string'
      if (isDate(value)) return 'date'
      return 'null'
    }
    case 'unary':
      return node[1] === '!' ? 'bool' : 'number'
    case 'call':
      return FUNCTIONS[node[1]][2]
    case 'binary':
      return CMP_OPS.has(node[1]) || node[1] === '&&' || node[1] === '||' ? 'bool' : 'number'
  }
}

function staticTypeErrors(node: AstNode): string[] {
  if (node[0] === 'lit' || node[0] === 'var') return []
  if (node[0] === 'unary') return staticTypeErrors(node[2])
  if (node[0] === 'call') {
    const errors = node[2].flatMap(staticTypeErrors)
    // today()/now() 依赖运行时钟，静态期不折叠（类型由 FUNCTIONS 表推断）。
    if (!NONDETERMINISTIC.has(node[1]) && !node[2].some(hasVar)) {
      try {
        evaluateConst(node)
      } catch (error) {
        errors.push(error instanceof Error ? error.message : String(error))
      }
    }
    return errors
  }
  const op = node[1]
  const errors = [...staticTypeErrors(node[2]), ...staticTypeErrors(node[3])]
  const subtreeHasVar = hasVar(node[2]) || hasVar(node[3])
  if (!subtreeHasVar) {
    try {
      if (CMP_OPS.has(op)) {
        compare(op, evaluateConst(node[2]), evaluateConst(node[3]))
      } else if (ADD_OPS.has(op) || MUL_OPS.has(op)) {
        evaluateConst(node)
      }
    } catch (error) {
      errors.push(error instanceof Error ? error.message : String(error))
    }
  }
  return errors
}

/** 校验期检查：语法 + 静态类型（常量折叠 + 顶层须为布尔）；返回中文错误列表。 */
export function validateExpression(expression: string): string[] {
  if (!expression.trim()) return ['表达式不能为空']
  let ast: AstNode
  try {
    ast = new Parser(tokenize(expression)).parse()
  } catch (error) {
    return [error instanceof Error ? error.message : String(error)]
  }
  const errors = staticTypeErrors(ast)
  const topType = inferType(ast)
  if (topType === 'number' || topType === 'string' || topType === 'date' || topType === 'datetime') {
    errors.push('条件表达式必须产出布尔值（比较或逻辑运算），不能直接使用算术结果/数值/字符串/日期')
  }
  return errors
}
