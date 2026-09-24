import { describe, expect, it } from 'vitest'

import { cleanText, mergeAuditPages, parseLocalInput, toUtcBound, utcBounds } from '../auditFilters'
import type { AuditEventItem } from '../apiClient'

/** docs/61 §4.3 打包 H H3：审计过滤边界换算与游标累积。 */

function row(id: string, seq: number): AuditEventItem {
  return {
    id,
    tenantId: 't1',
    actor: 'admin-a',
    action: 'POST /api/x',
    statusCode: 200,
    path: '/api/x',
    ip: '127.0.0.1',
    at: '2026-09-01T12:00:00+00:00',
    seq,
  }
}

describe('toUtcBound', () => {
  it('把本地时刻换算成 UTC ISO-8601（+00:00 结尾）', () => {
    const local = new Date(Date.UTC(2026, 8, 1, 12, 0, 0))
    expect(toUtcBound(local)).toBe('2026-09-01T12:00:00.000+00:00')
  })

  it('空值返回 undefined（只填一端也允许）', () => {
    expect(toUtcBound(null)).toBeUndefined()
    expect(toUtcBound(undefined)).toBeUndefined()
  })
})

describe('parseLocalInput 与 utcBounds', () => {
  it('空值与非法串解析为 null，不抛', () => {
    expect(parseLocalInput('')).toBeNull()
    expect(parseLocalInput(undefined)).toBeNull()
    expect(parseLocalInput('not-a-date')).toBeNull()
  })

  it('datetime-local 的本地无时区串按本地时区解析', () => {
    const parsed = parseLocalInput('2026-09-25T10:30:00')
    expect(parsed).not.toBeNull()
    expect(parsed!.getHours()).toBe(10)
    expect(parsed!.getMonth()).toBe(8)
  })

  it('两端都空 → 空对象，不发多余参数', () => {
    expect(utcBounds('', '')).toEqual({})
    expect(utcBounds('garbage', '')).toEqual({})
  })

  it('只填起始 → 只有 since，且换算成 UTC 且以 +00:00 结尾', () => {
    const bounds = utcBounds('2026-09-25T10:30:00', '')
    expect(Object.keys(bounds)).toEqual(['since'])
    expect(bounds.since?.endsWith('+00:00')).toBe(true)
  })

  it('两端都填 → since 早于 until', () => {
    const bounds = utcBounds('2026-09-01T00:00:00', '2026-09-02T23:59:59')
    expect(new Date(bounds.since!) < new Date(bounds.until!)).toBe(true)
  })
})

describe('cleanText', () => {
  it('空白与缺省都归一为 undefined', () => {
    expect(cleanText('   ')).toBeUndefined()
    expect(cleanText(undefined)).toBeUndefined()
    expect(cleanText(' admin-a ')).toBe('admin-a')
  })
})

describe('mergeAuditPages', () => {
  it('首页直接采用', () => {
    const first = [row('a-2', 2), row('a-1', 1)]
    expect(mergeAuditPages([], first)).toBe(first)
  })

  it('追加下一页并按 id 去重（同页重复请求不插重复行）', () => {
    const current = [row('a-3', 3)]
    const merged = mergeAuditPages(current, [row('a-3', 3), row('a-2', 2)])
    expect(merged.map((item) => item.id)).toEqual(['a-3', 'a-2'])
  })

  it('全部重复时返回原数组引用（不制造新渲染）', () => {
    const current = [row('a-1', 1)]
    expect(mergeAuditPages(current, [row('a-1', 1)])).toBe(current)
  })
})
