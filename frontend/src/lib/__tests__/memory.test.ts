import { describe, expect, it } from 'vitest'
import {
  formatConfidence,
  formatCreatedAt,
  formatScope,
  formatScore,
  kindColor,
  kindLabel,
  MEMORY_KIND_COLORS,
  MEMORY_KIND_LABELS,
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
