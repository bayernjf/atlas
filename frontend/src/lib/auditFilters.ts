/**
 * 审计页过滤条件的纯逻辑（docs/61 §4.3）。
 *
 * RangePicker 给出的是**本地时区**的 Date，后端 `at` 是 UTC ISO-8601（`+00:00`），
 * 故边界必须显式换算——直接传本地字符串会让「今天」查成别的时区那一天。
 */

export type AuditRange = [Date | null, Date | null] | null

/** 本地 Date → UTC ISO-8601（+00:00 结尾，与后端存储同格式）；空值返回 undefined。 */
export function toUtcBound(value: Date | null | undefined): string | undefined {
  if (!value) return undefined
  return value.toISOString().replace('Z', '+00:00')
}

/** 时间区间 → since/until；只填一端也可（闭区间语义在后端）。 */
export function auditRangeBounds(range: AuditRange): {
  since?: string
  until?: string
} {
  if (!range) return {}
  const [from, to] = range
  const since = toUtcBound(from)
  const until = toUtcBound(to)
  const bounds: { since?: string; until?: string } = {}
  if (since) bounds.since = since
  if (until) bounds.until = until
  return bounds
}

/** 空白视为不过滤（后端把空串按未传处理，前端也归一，避免发 `actor=` 噪声）。 */
export function cleanText(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

/**
 * 游标翻页的累积规则：刷新/改过滤时替换整页，「加载更多」时按 seq 去重后追加，
 * 保证同一页被重复请求也不会插入重复行。
 */
export function mergeAuditPages<T extends { id: string; seq: number }>(
  current: T[],
  incoming: T[],
): T[] {
  if (current.length === 0) return incoming
  const seen = new Set(current.map((item) => item.id))
  const appended = incoming.filter((item) => !seen.has(item.id))
  return appended.length === 0 ? current : [...current, ...appended]
}
