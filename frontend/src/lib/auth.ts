/**
 * 登录会话与角色（04 §5.14）：进程内 sess-token 存 localStorage，
 * 三角色端点级 RBAC 的前端镜像。持久化账号/注册流随 S1/D22 缓做。
 * 角色中文标签的单一事实源在 locales/zh-CN/common.json 的 role.*（docs/17 §2.3）。
 */
import zhCommon from '../locales/zh-CN/common.json'

export type Role = 'viewer' | 'operator' | 'admin'
export type Capability = 'read' | 'operate' | 'administer'

export type Principal = {
  tenant_id: string
  tenant_name: string
  username: string
  display_name: string
  role: Role
}

export type LoginResponse = {
  token: string
  principal: Principal
}

export const TOKEN_KEY = 'atlas.session_token'
const PRINCIPAL_KEY = 'atlas.principal'

export const UNAUTHORIZED_EVENT = 'atlas:unauthorized'

const ROLE_RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 }
const CAPABILITY_RANK: Record<Capability, number> = { read: 1, operate: 2, administer: 3 }

export function roleCan(role: Role, capability: Capability): boolean {
  return ROLE_RANK[role] >= CAPABILITY_RANK[capability]
}

export const ROLE_LABELS: Record<Role, string> = {
  viewer: zhCommon.role.viewer,
  operator: zhCommon.role.operator,
  admin: zhCommon.role.admin,
}

export function saveSession(token: string, principal: Principal): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(PRINCIPAL_KEY, JSON.stringify(principal))
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function getStoredPrincipal(): Principal | null {
  try {
    const raw = localStorage.getItem(PRINCIPAL_KEY)
    if (!raw) return null
    const principal = JSON.parse(raw) as Principal
    return ROLE_RANK[principal.role] ? principal : null
  } catch {
    return null
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(PRINCIPAL_KEY)
  } catch {
    // 无 localStorage（测试环境）时无需清理
  }
}

/** 401 统一处理：清会话并广播，App 收到后回登录页（04 §5.14）。 */
export function handleUnauthorized(): void {
  clearSession()
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
  }
}
