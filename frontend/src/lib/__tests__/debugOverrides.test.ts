import { describe, expect, it } from 'vitest'
import { parseGlobalsDraft } from '../debugOverrides'

// B 包（docs/27 §4.3，U137）：暂停续跑 globals 覆盖草稿解析/校验。
describe('parseGlobalsDraft (docs/27 §4.3)', () => {
  it('空白草稿视为不覆盖，返回空对象', () => {
    expect(parseGlobalsDraft('')).toEqual({ ok: true, value: {} })
    expect(parseGlobalsDraft('   \n ')).toEqual({ ok: true, value: {} })
  })

  it('合法对象原样返回（含数字/字符串/布尔/嵌套值）', () => {
    const result = parseGlobalsDraft('{"amount": 9999, "tier": "big", "flag": true}')
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value).toEqual({ amount: 9999, tier: 'big', flag: true })
    }
  })

  it('非法 JSON 报错', () => {
    const result = parseGlobalsDraft('{amount: 1}')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain('合法 JSON')
  })

  it('数组/数字/null/字符串顶层值被拒（必须是对象）', () => {
    expect(parseGlobalsDraft('[1,2,3]').ok).toBe(false)
    expect(parseGlobalsDraft('123').ok).toBe(false)
    expect(parseGlobalsDraft('null').ok).toBe(false)
    expect(parseGlobalsDraft('"x"').ok).toBe(false)
  })

  it('非法标识符键名被拒（与后端 ^[A-Za-z_][A-Za-z0-9_]*$ 一致）', () => {
    expect(parseGlobalsDraft('{"1x": 1}').ok).toBe(false)
    expect(parseGlobalsDraft('{"a-b": 1}').ok).toBe(false)
    const empty = parseGlobalsDraft('{"": 1}')
    expect(empty.ok).toBe(false)
    if (!empty.ok) expect(empty.error).toContain('非法 global 变量名')
  })

  it('合法下划线/字母开头键通过', () => {
    expect(parseGlobalsDraft('{"_a": 1, "amount": 2}').ok).toBe(true)
  })
})
