/**
 * 登录会话与角色（04 §5.14）：打包 ZQ Q4 起会话凭证由后端以 httpOnly Cookie
 * （atlas_session，HttpOnly＋SameSite=Strict＋prod Secure）下发，前端**不再落盘 token**——
 * 浏览器自动携带 Cookie，前端只保留角色/租户等非敏感 Principal 镜像。
 * Bearer 仅剩 A2A/MCP 与旧调用方使用（后端仍兼容）。
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

export function saveSession(_token: string, principal: Principal): void {
  // 打包 ZQ Q4：token 不再写入 localStorage——会话凭证由后端 httpOnly Cookie 持有，
  // 前端代码与脚本都读不到它（XSS 无法窃取）。_token 以下划线开头：签名保留以兼容
  // LoginResponse 形状，值不使用（tsc noUnusedParameters 豁免）。
  localStorage.setItem(PRINCIPAL_KEY, JSON.stringify(principal))
}

export function getToken(): string | null {
  // 打包 ZQ Q4：前端不再持有 token。保留函数名与签名，使既有调用点（apiClient 的
  // Authorization 头注入）自然失效——请求由浏览器自动携带 httpOnly Cookie。
  return null
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
