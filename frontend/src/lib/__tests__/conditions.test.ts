import { describe, expect, it } from 'vitest'
import { validateExpression } from '../conditions'

describe('validateExpression', () => {
  it('accepts the first-version whitelist', () => {
    expect(validateExpression('{{trigger-1.context.payload.amount}} > 1000')).toEqual([])
    expect(validateExpression("{{status}} == 'paid' && {{x}} != null")).toEqual([])
    expect(validateExpression('!({{a}} >= 10) || {{b}} < -5')).toEqual([])
  })

  it('reports syntax errors', () => {
    expect(validateExpression('amount >')[0]).toContain('语法错误')
    expect(validateExpression('foo == 1')[0]).toContain('未知标识符')
    expect(validateExpression('(1 > 2')[0]).toContain('括号')
    expect(validateExpression('1 + 1')[0]).toContain('语法错误')
    expect(validateExpression('   ')).toEqual(['表达式不能为空'])
  })

  it('statically rejects literal-only type mismatches but allows variable cases', () => {
    expect(validateExpression("'a' > 1")[0]).toContain('同为数字或同为字符串')
    expect(validateExpression('{{name}} > 1')).toEqual([])
  })

  it('checks precedence and null ordering statically', () => {
    expect(validateExpression('null > 1')[0]).toContain('空值')
    expect(validateExpression('true > false')[0]).toContain('布尔')
  })
})
