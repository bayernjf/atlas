import { describe, expect, it } from 'vitest'
import { paramsToText, parseParamsObject } from '../params'
import { setAtPath } from '../formTree'

// 内置模板 sql-query-notify 的真实 params：绑定值上带裸 {{}} 插值
const BARE_INTERPOLATION = '{"sql":"SELECT order_id FROM orders WHERE amount > :min","params":{"min":{{trigger-1.context.payload.min_amount}}},"limit":50}'

describe('parseParamsObject：JSON 字符串存储形态（U39③）', () => {
  it('空文本视为空对象（新建节点初始态）', () => {
    expect(parseParamsObject('')).toEqual({})
    expect(parseParamsObject('   ')).toEqual({})
    expect(parseParamsObject(undefined)).toEqual({})
  })

  it('对象文本解析为对象（含嵌套与字符串值内 {{}}）', () => {
    const text = '{"sql":"SELECT 1","params":{"min":1000},"limit":500}'
    expect(parseParamsObject(text)).toEqual({ sql: 'SELECT 1', params: { min: 1000 }, limit: 500 })
    expect(parseParamsObject('{"subject":"订单 {{trigger-1.context.payload.order_id}} 待审批"}')).toEqual({
      subject: '订单 {{trigger-1.context.payload.order_id}} 待审批',
    })
  })

  it('裸 {{}} 插值的旧参数不可解析 → 停留 JSON 文本框（不做部分解析）', () => {
    expect(parseParamsObject(BARE_INTERPOLATION)).toBeNull()
  })

  it('语法错误与非对象结果一律 null', () => {
    expect(parseParamsObject('{"sql":')).toBeNull()
    expect(parseParamsObject('[1,2]')).toBeNull()
    expect(parseParamsObject('123')).toBeNull()
    expect(parseParamsObject('"GET"')).toBeNull()
    expect(parseParamsObject('{{trigger-1.result}}')).toBeNull()
  })
})

describe('paramsToText：回写仍是 JSON 字符串（U39④）', () => {
  it('对象序列化为 JSON 字符串', () => {
    expect(paramsToText({ sql: 'SELECT 1', limit: 50 })).toBe('{"sql":"SELECT 1","limit":50}')
  })

  it('undefined 回写为空串（清空参数）', () => {
    expect(paramsToText(undefined)).toBe('')
  })

  it('编辑结果经 stringify → parse 可原样往返', () => {
    const parsed = parseParamsObject(BARE_INTERPOLATION.replace('{{trigger-1.context.payload.min_amount}}', '1000'))
    expect(parsed).not.toBeNull()
    const edited = setAtPath(parsed, ['params', 'min'], 2000)
    const text = paramsToText(edited)
    expect(parseParamsObject(text)).toEqual({
      sql: 'SELECT order_id FROM orders WHERE amount > :min',
      params: { min: 2000 },
      limit: 50,
    })
  })

  it('字符串值内 {{}} 在字符串化后仍是合法 JSON 字符串', () => {
    const value = { subject: '订单 {{trigger-1.context.payload.order_id}} 待审批' }
    const text = paramsToText(value)
    expect(typeof text).toBe('string')
    expect(parseParamsObject(text)).toEqual(value)
  })
})
