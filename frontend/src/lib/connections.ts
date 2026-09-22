/**
 * T4 OAuth2 连接管理前端纯函数（docs/35 §4.6）：状态标签/颜色、scopes 文本转换、
 * 表单 draft 校验与后端 payload 构造。抽离为无副作用纯函数便于 vitest 对拍。
 */
import type { ConnectionInput, ConnectionStatus } from './apiClient'

// 状态展示标签映射到 connections namespace 的 i18n key（纯函数不引 hook，由页面 t() 解析）。
export const CONNECTION_STATUS_LABELS: Record<ConnectionStatus, string> = {
  draft: 'status.draft',
  connected: 'status.connected',
  error: 'status.error',
}

export const CONNECTION_STATUS_COLORS: Record<ConnectionStatus, string> = {
  draft: 'default',
  connected: 'green',
  error: 'red',
}

export function statusLabel(status: ConnectionStatus): string {
  return CONNECTION_STATUS_LABELS[status] ?? status
}

export function statusColor(status: ConnectionStatus): string {
  return CONNECTION_STATUS_COLORS[status] ?? 'default'
}

/** scopes 数组 → 空格分隔文本（编辑回填）。 */
export function scopesToText(scopes: string[] | null | undefined): string {
  return (scopes ?? []).join(' ')
}

/** 空格/逗号（含中文逗号）分隔文本 → 去重 scopes 数组。 */
export function parseScopesText(text: string): string[] {
  const parts = text.split(/[\s,，]+/).filter(Boolean)
  return Array.from(new Set(parts))
}

/** UTC ISO → 本地「YYYY-MM-DD HH:mm:ss」；空/非法返回空串。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

export type ConnectionDraft = {
  provider: string
  displayName: string
  authUrl: string
  tokenUrl: string
  clientId: string
  clientSecret: string
  scopesText: string
  redirectUri: string
}

export const EMPTY_CONNECTION_DRAFT: ConnectionDraft = {
  provider: '',
  displayName: '',
  authUrl: '',
  tokenUrl: '',
  clientId: '',
  clientSecret: '',
  scopesText: '',
  redirectUri: '',
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}

/**
 * 校验 draft 并构造后端 payload。
 * - 创建：provider/displayName/authUrl/tokenUrl/clientId 必填，URL 须 http(s)；
 * - 编辑（isEdit）：clientSecret 可空（空＝保留原信封）；
 * - redirectUri 留空则不传（后端落默认回调地址）。
 */
export function buildConnectionInput(
  draft: ConnectionDraft,
  opts: { isEdit: boolean },
): { input?: ConnectionInput; error?: string } {
  const provider = draft.provider.trim()
  const displayName = draft.displayName.trim()
  const authUrl = draft.authUrl.trim()
  const tokenUrl = draft.tokenUrl.trim()
  const clientId = draft.clientId.trim()
  const redirectUri = draft.redirectUri.trim()

  if (!provider) return { error: '平台标识（provider）不能为空' }
  if (!displayName) return { error: '展示名不能为空' }
  if (!authUrl || !isHttpUrl(authUrl)) return { error: '授权端点（authUrl）必须是合法 http(s) URL' }
  if (!tokenUrl || !isHttpUrl(tokenUrl)) return { error: '令牌端点（tokenUrl）必须是合法 http(s) URL' }
  if (!clientId) return { error: 'clientId 不能为空' }
  if (!opts.isEdit && !draft.clientSecret) {
    // 公共客户端可无 secret：此处仅警告不阻断——允许空 secret 创建（hasClientSecret=false）。
  }

  const input: ConnectionInput = {
    provider,
    displayName,
    authUrl,
    tokenUrl,
    clientId,
    scopes: parseScopesText(draft.scopesText),
  }
  if (draft.clientSecret) input.clientSecret = draft.clientSecret
  if (redirectUri) {
    if (!isHttpUrl(redirectUri)) return { error: '回调地址（redirectUri）必须是合法 http(s) URL' }
    input.redirectUri = redirectUri
  }
  return { input }
}
