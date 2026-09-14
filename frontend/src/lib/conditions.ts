/**
 * condition 节点首版规则表达式（04 §5.1 语法白名单 / §5.2 契约）。
 *
 * 与后端 atlas.graph.conditions 同构的手写递归下降解析，禁止 eval、零依赖。
 * 前端只做语法 + 纯字面量静态类型校验（实时中文报错）；求值在后端进行，
 * 含变量的运行时类型错误由后端 fail-safe 走 defaultTarget。
 */

const TOKEN_RE =
  /\{\{\s*[^{}]+?\s*\}\}|>=|<=|==|!=|&&|\|\||[()!><]|-?\d+(?:\.\d+)?|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*/gy

type Token = { kind: string; value: string; pos: number }
type AstNode =
  | ['lit', unknown]
  | ['var', string]
  | ['unary', 'not', AstNode]
  | ['binary', string, AstNode, AstNode]

const OPS = new Set(['>', '>=', '<', '<=', '==', '!='])

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
    else if (/^-?\d/.test(text)) tokens.push({ kind: 'number', value: text, pos })
    else if (text[0] === "'" || text[0] === '"') tokens.push({ kind: 'string', value: text.slice(1, -1), pos })
    else if (text === 'true' || text === 'false' || text === 'null') {
      tokens.push({ kind: 'literal', value: text, pos })
    } else if (text === '&&' || text === '||' || OPS.has(text) || text === '!' || text === '(' || text === ')') {
      tokens.push({ kind: 'op', value: text, pos })
    } else {
      throw new Error(
        `语法错误：未知标识符 "${text}"（位置 ${pos + 1}；变量须用 {{路径}} 包裹，字面量仅支持 true/false/null、数字、字符串）`,
      )
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
      return ['unary', 'not', this.parseNot()]
    }
    return this.parseComparison()
  }

  private parseComparison(): AstNode {
    const left = this.parsePrimary()
    const token = this.peek()
    if (token && OPS.has(token.value)) {
      this.consume()
      return ['binary', token.value, left, this.parsePrimary()]
    }
    return left
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
    if (token.kind === 'number') return ['lit', token.value.includes('.') ? Number(token.value) : parseInt(token.value, 10)]
    if (token.kind === 'string') return ['lit', token.value]
    if (token.kind === 'literal') {
      return ['lit', token.value === 'true' ? true : token.value === 'false' ? false : null]
    }
    throw new Error(`语法错误：意外的 token "${token.value}"（位置 ${token.pos + 1}）`)
  }
}

function hasVar(node: AstNode): boolean {
  if (node[0] === 'var') return true
  if (node[0] === 'lit') return false
  if (node[0] === 'unary') return hasVar(node[2])
  return hasVar(node[2]) || hasVar(node[3])
}

/** 校验期检查：语法 + 纯字面量比较的静态类型；返回中文错误列表。 */
export function validateExpression(expression: string): string[] {
  if (!expression.trim()) return ['表达式不能为空']
  let ast: AstNode
  try {
    ast = new Parser(tokenize(expression)).parse()
  } catch (error) {
    return [error instanceof Error ? error.message : String(error)]
  }
  return staticTypeErrors(ast)
}

function staticTypeErrors(node: AstNode): string[] {
  if (node[0] === 'lit' || node[0] === 'var') return []
  if (node[0] === 'unary') return staticTypeErrors(node[2])
  const errors = [...staticTypeErrors(node[2]), ...staticTypeErrors(node[3])]
  const op = node[1]
  if (['>', '>=', '<', '<='].includes(op) && !hasVar(node[2]) && !hasVar(node[3])) {
    const left = literalValue(node[2])
    const right = literalValue(node[3])
    if (left === null || right === null) {
      errors.push('空值（null）只能做 == / != 比较，不能参与大小比较')
    } else if (typeof left !== typeof right || typeof left === 'boolean') {
      errors.push(`有序比较 "${op}" 要求两侧同为数字或同为字符串，实际为 ${typeName(left)} 与 ${typeName(right)}`)
    }
  }
  return errors
}

function literalValue(node: AstNode): unknown {
  return node[0] === 'lit' ? node[1] : true
}

function typeName(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return '布尔'
  if (typeof value === 'number') return '数字'
  if (typeof value === 'string') return '字符串'
  return typeof value
}
