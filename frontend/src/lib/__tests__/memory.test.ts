import { describe, expect, it } from 'vitest'
import {
  buildMemoryPayload,
  formatConfidence,
  formatCreatedAt,
  formatScope,
  formatScore,
  kindColor,
  kindLabel,
  MEMORY_KIND_COLORS,
  MEMORY_KIND_LABELS,
  parseStringMapText,
} from '../memory'

describe('M11 memory 纯函数（U98）', () => {
  it('kind 标签：fact 事实 / preference 偏好', () => {
    expect(kindLabel('fact')).toBe('事实')
    expect(kindLabel('preference')).toBe('偏好')
    expect(MEMORY_KIND_LABELS.fact).toBe('事实')
  })

  it('kind 颜色：fact 蓝 / preference 紫', () => {
    expect(kindColor('fact')).toBe('blue')
    expect(kindColor('preference')).toBe('purple')
    expect(MEMORY_KIND_COLORS.preference).toBe('purple')
  })

  it('未知 kind 兜底（不抛错）', () => {
    // 后端契约只允许 fact/preference；前端对异常值安全兜底
    expect(kindLabel('other' as never)).toBe('other')
    expect(kindColor('other' as never)).toBe('default')
  })

  it('formatScore：余弦 → 百分比整数并 clamp 到 0–1', () => {
    expect(formatScore(0)).toBe('0%')
    expect(formatScore(0.434524)).toBe('43%')
    expect(formatScore(1)).toBe('100%')
    expect(formatScore(-0.2)).toBe('0%')
    expect(formatScore(1.5)).toBe('100%')
  })

  it('formatConfidence：两位小数', () => {
    expect(formatConfidence(1)).toBe('1.00')
    expect(formatConfidence(0.876)).toBe('0.88')
    expect(formatConfidence(0)).toBe('0.00')
  })

  it('formatScope：键值对扁平化，空 scope 返空串', () => {
    expect(formatScope({ order_id: 'o-1' })).toBe('order_id=o-1')
    expect(formatScope({ user_id: 'u-1', channel: 'sms' })).toBe('user_id=u-1，channel=sms')
    expect(formatScope({})).toBe('')
    expect(formatScope(null)).toBe('')
    expect(formatScope(undefined)).toBe('')
  })

  it('formatCreatedAt：合法 ISO 含日期与时间，非法原样返回', () => {
    const formatted = formatCreatedAt('2026-09-19T13:42:04.873170+00:00')
    expect(formatted).toMatch(/^2026-09-\d{2} \d{2}:\d{2}:\d{2}$/)
    expect(formatCreatedAt('not-a-date')).toBe('not-a-date')
  })
})

describe('记忆手动新建/编辑表单纯函数（⑩）', () => {
  it('parseStringMapText：空文本→{}，合法对象解析', () => {
    for (const text of ['', '   ']) {
      const empty = parseStringMapText(text)
      expect(empty.ok).toBe(true)
      if (empty.ok) expect(empty.value).toEqual({})
    }
    const obj = parseStringMapText('{"user_id":"u-1"}')
    expect(obj.ok).toBe(true)
    if (obj.ok) expect(obj.value).toEqual({ user_id: 'u-1' })
  })

  it('parseStringMapText：非法 JSON / 数组 / 非字符串值报错', () => {
    expect(parseStringMapText('{a:').ok).toBe(false)
    const arr = parseStringMapText('["a","b"]')
    expect(arr.ok).toBe(false)
    const num = parseStringMapText('123')
    expect(num.ok).toBe(false)
    const badVal = parseStringMapText('{"k":1}')
    expect(badVal.ok).toBe(false)
    const emptyKey = parseStringMapText('{"":"v"}')
    expect(emptyKey.ok).toBe(false)
  })

  it('buildMemoryPayload：合法表单构造 payload（content trim、空 map 归 {}）', () => {
    const result = buildMemoryPayload({
      kind: 'preference',
      content: '  手动偏好  ',
      confidence: 0.5,
      scopeText: '{"user_id":"u-7"}',
      metadataText: '',
    })
    expect(result.error).toBeUndefined()
    expect(result.payload).toEqual({
      kind: 'preference',
      content: '手动偏好',
      confidence: 0.5,
      scope: { user_id: 'u-7' },
      metadata: {},
    })
  })

  it('buildMemoryPayload：空内容/超长/越界置信度/坏 JSON 返中文错误且无 payload', () => {
    expect(buildMemoryPayload({
      kind: 'fact', content: '   ', confidence: 1, scopeText: '', metadataText: '',
    }).payload).toBeUndefined()
    expect(buildMemoryPayload({
      kind: 'fact', content: 'x'.repeat(2001), confidence: 1, scopeText: '', metadataText: '',
    }).error).toContain('2000')
    expect(buildMemoryPayload({
      kind: 'fact', content: 'ok', confidence: 1.4, scopeText: '', metadataText: '',
    }).error).toContain('置信度')
    const badJson = buildMemoryPayload({
      kind: 'fact', content: 'ok', confidence: 1, scopeText: '{bad', metadataText: '',
    })
    expect(badJson.payload).toBeUndefined()
    expect(badJson.error).toContain('作用域')
  })
})

