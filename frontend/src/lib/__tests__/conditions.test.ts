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
    expect(validateExpression('   ')).toEqual(['表达式不能为空'])
  })

  it('statically rejects literal-only type mismatches but allows variable cases', () => {
    expect(validateExpression("'a' > 1")[0]).toContain('同为数字')
    expect(validateExpression('{{name}} > 1')).toEqual([])
  })

  it('checks precedence and null ordering statically', () => {
    expect(validateExpression('null > 1')[0]).toContain('空值')
    expect(validateExpression('true > false')[0]).toContain('布尔')
  })

  it('D15 accepts arithmetic inside comparisons', () => {
    expect(validateExpression('1 + 2 * 3 == 7')).toEqual([])
    expect(validateExpression('(1 + 2) * 3 == 9')).toEqual([])
    expect(validateExpression('10 / 4 == 2.5')).toEqual([])
    expect(validateExpression('7 % 3 == 1')).toEqual([])
    expect(validateExpression('-7 % 3 == -1')).toEqual([])
    expect(validateExpression('{{x}} * 2 > 10')).toEqual([])
    expect(validateExpression('-5 < 1')).toEqual([])
  })

  it('D15 rejects non-boolean top-level arithmetic/constants', () => {
    expect(validateExpression('1 + 1')[0]).toContain('布尔值')
    expect(validateExpression('-5 + 1')[0]).toContain('布尔值')
    expect(validateExpression("'hello'")[0]).toContain('布尔值')
    expect(validateExpression('abs(-3)')[0]).toContain('布尔值')
    expect(validateExpression('date(2026,1,1)')[0]).toContain('布尔值')
    expect(validateExpression('{{x}} + 1')[0]).toContain('布尔值')
    expect(validateExpression('{{x}}')).toEqual([])
  })

  it('D15 constant-folds arithmetic type and divide-by-zero errors', () => {
    expect(validateExpression('1 / 0 == 0')[0]).toContain('除数不能为 0')
    expect(validateExpression('1 % 0 == 0')[0]).toContain('模数不能为 0')
    expect(validateExpression("'a' + 1 == 'a1'")[0]).toContain('均为数值')
    expect(validateExpression('{{s}} + 1 == 2')).toEqual([])
  })

  it('D15 accepts numeric/string whitelist functions inside comparisons', () => {
    expect(validateExpression('abs(-3) == 3')).toEqual([])
    expect(validateExpression('floor(2.8) == 2')).toEqual([])
    expect(validateExpression('ceil(2.1) == 3')).toEqual([])
    expect(validateExpression('round(2.5) == 3')).toEqual([])
    expect(validateExpression('round(-1.5) == -1')).toEqual([])
    expect(validateExpression('min(3, 1, 2) == 1')).toEqual([])
    expect(validateExpression('max(3, 1, 2) == 3')).toEqual([])
    expect(validateExpression("len('hello') == 5")).toEqual([])
    expect(validateExpression("lower('AbC') == 'abc'")).toEqual([])
    expect(validateExpression("upper('AbC') == 'ABC'")).toEqual([])
    expect(validateExpression('len(1) == 1')[0]).toContain('字符串/数组/对象')
  })

  it('D15 handles deterministic date functions and comparisons', () => {
    expect(validateExpression('date(2026, 1, 1) < date(2026, 2, 1)')).toEqual([])
    expect(validateExpression('year(date(2026, 9, 19)) == 2026')).toEqual([])
    expect(validateExpression('daysBetween(date(2026,1,1), date(2026,1,11)) == 10')).toEqual([])
    expect(validateExpression('daysBetween(date(2026,1,11), date(2026,1,1)) == -10')).toEqual([])
    expect(validateExpression('date(2026, 13, 1) < date(2027,1,1)')[0]).toContain('非法日期')
    expect(validateExpression('date(2026,1,1) > 1')[0]).toContain('同为数字')
    expect(validateExpression('year(1) == 2026')[0]).toContain('要求日期值')
  })

  it('D15 rejects unknown functions and bad arity', () => {
    expect(validateExpression('foo(1) == 1')[0]).toContain('未知函数')
    expect(validateExpression('abs() == 0')[0]).toContain('参数')
    expect(validateExpression('date(2026, 1) == date(2026,1)')[0]).toContain('3 个参数')
    expect(validateExpression('bar == 1')[0]).toContain('未知标识符')
  })
})
