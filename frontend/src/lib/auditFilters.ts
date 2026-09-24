/**
 * 审计页过滤条件的纯逻辑（docs/61 §4.3）。
 *
 * 时间输入用原生 `datetime-local`（无时区、按本地时区呈现），后端 `at` 是 UTC
 * ISO-8601（`+00:00`），故边界必须显式换算——直接把本地串丢给后端会让「今天」
 * 查成别的时区那一天。刻意不用 AntD RangePicker：它会把 dayjs 拖成直接依赖，
 * 且中文面板需另配 dayjs locale，见 docs/61 收口注记。
 */

/** 本地 Date → UTC ISO-8601（+00:00 结尾，与后端存储同格式）；空值返回 undefined。 */
export function toUtcBound(value: Date | null | undefined): string | undefined {
  if (!value) return undefined
  return value.toISOString().replace('Z', '+00:00')
}

/**
 * `<input type="datetime-local">` 的值（`2026-09-25T10:30`，无时区后缀）按**本地时区**解析成 Date。
 * 浏览器对无时区的 date-time 串一律按本地时间处理，正是我们要的语义（用户看到的是本地时刻）。
 * 空值与非法值返回 null，不抛。
 */
export function parseLocalInput(value: string | undefined | null): Date | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/**
 * 两个本地无时区串（datetime-local 的值）→ 后端要的 UTC ISO 边界。
 * 只填一端也可以；空值/非法值直接不产生参数（后端按未传处理）。
 */
export function utcBounds(
  sinceValue: string | undefined | null,
  untilValue: string | undefined | null,
): { since?: string; until?: string } {
  const out: { since?: string; until?: string } = {}
  const since = toUtcBound(parseLocalInput(sinceValue))
  const until = toUtcBound(parseLocalInput(untilValue))
  if (since) out.since = since
  if (until) out.until = until
  return out
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
