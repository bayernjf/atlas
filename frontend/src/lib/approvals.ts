/**
 * 审批闭环批纯逻辑（docs/36 §3-§5）：深链 token 解析、剩余时间计算与格式化。
 */

const EMAIL_LINK_PREFIX = '/approvals/'

/** pathname 以 /approvals/ 开头时取末段签名 token；空段/不匹配返回 null。 */
export function extractEmailToken(pathname: string): string | null {
  if (!pathname.startsWith(EMAIL_LINK_PREFIX)) return null
  const segment = pathname.slice(EMAIL_LINK_PREFIX.length).split('/')[0]
  return segment ? decodeURIComponent(segment) : null
}

/** 剩余秒：createdAt + timeoutSeconds - now，下限 0。 */
export function remainingSeconds(
  createdAt: number,
  timeoutSeconds: number,
  now: number = Date.now() / 1000,
): number {
  return Math.max(0, createdAt + timeoutSeconds - now)
}

/** 剩余时间中文格式：>90s 显示「X 分 Y 秒」，否则「X 秒」；0 显示「已超时」。 */
export function formatRemaining(seconds: number): string {
  if (seconds <= 0) return '已超时'
  const minutes = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  if (minutes === 0) return `${rest} 秒`
  return rest === 0 ? `${minutes} 分` : `${minutes} 分 ${rest} 秒`
}

/** epoch 秒 → 本地时间字符串（队列「创建时间」列）。 */
/**
 * 兼容两种时刻形状：pending 投影的 float epoch 秒，与已决历史的 UTC ISO-8601 串
 * （docs/61 §3.3——已决 createdAt 改 ISO、并新增 resolvedAt）。
 */
export function formatCreatedAt(value: string | number): string {
  if (typeof value === 'number') return value ? new Date(value * 1000).toLocaleString() : ''
  if (!value) return ''
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}
