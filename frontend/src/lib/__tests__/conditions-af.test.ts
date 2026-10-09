/** 打包 AF（docs/112）U1305：choice/weightedChoice 类型面 ＋ 命名时区同构。 */

import { describe, expect, it } from 'vitest'

import {
  evaluateConstantExpression,
  validateExpression,
} from '../conditions'

const AT_16 = 'datetime(2026,6,15,16,0)'
const AT_3 = 'datetime(2026,6,15,3,0)'

describe('dateOfInZone 常量折叠（与后端同值）', () => {
  it('跨日界返回正确日期', () => {
    expect(evaluateConstantExpression(`dateOfInZone(${AT_16},"Asia/Shanghai")`)).toEqual({
      __date: true, y: 2026, m: 6, d: 16,
    })
    expect(evaluateConstantExpression(`dateOfInZone(${AT_16},"America/New_York")`)).toEqual({
      __date: true, y: 2026, m: 6, d: 15,
    })
    expect(evaluateConstantExpression(`dateOfInZone(${AT_3},"Asia/Shanghai")`)).toEqual({
      __date: true, y: 2026, m: 6, d: 15,
    })
    expect(evaluateConstantExpression(`dateOfInZone(${AT_3},"America/New_York")`)).toEqual({
      __date: true, y: 2026, m: 6, d: 14,
    })
  })
})

describe('hourOfInZone 常量折叠（与后端同值）', () => {
  it('返回目标时区小时（含 0 点）', () => {
    expect(evaluateConstantExpression(`hourOfInZone(${AT_16},"Asia/Shanghai")`)).toBe(0)
    expect(evaluateConstantExpression(`hourOfInZone(${AT_16},"America/New_York")`)).toBe(12)
    expect(evaluateConstantExpression(`hourOfInZone(${AT_3},"Asia/Shanghai")`)).toBe(11)
    expect(evaluateConstantExpression(`hourOfInZone(${AT_3},"America/New_York")`)).toBe(23)
  })

  it('UTC 时区与输入同时刻', () => {
    expect(evaluateConstantExpression(`hourOfInZone(${AT_16},"UTC")`)).toBe(16)
  })
})

describe('非法时区', () => {
  it('确定性函数折叠时报错', () => {
    const errors = validateExpression(`dateOfInZone(${AT_16},"Not/AZone") == false`)
    expect(errors.some((m) => m.includes('时区'))).toBe(true)
  })

  it('空时区名报错', () => {
    const errors = validateExpression(`hourOfInZone(${AT_16},"") == false`)
    expect(errors.length).toBeGreaterThan(0)
  })
})

describe('choice / weightedChoice 类型面', () => {
  it('顶层随机选择必须产出布尔', () => {
    expect(validateExpression('choice("a","b")').some((m) => m.includes('布尔'))).toBe(true)
    expect(validateExpression('weightedChoice("a",9,"b",1)').some((m) => m.includes('布尔'))).toBe(true)
  })

  it('零参/缺参 arity 错误', () => {
    expect(validateExpression('choice()').length).toBeGreaterThan(0)
    expect(validateExpression('weightedChoice("a")').length).toBeGreaterThan(0)
  })

  it('非确定函数不进入常量求值', () => {
    expect(() => evaluateConstantExpression('choice(1,2)')).toThrow()
    expect(() => evaluateConstantExpression('todayInZone("UTC")')).toThrow()
  })

  it('用于比较的合法确定性表达式无错误', () => {
    expect(validateExpression(`hourOfInZone(${AT_16},"Asia/Shanghai") >= 9`)).toEqual([])
  })
})
