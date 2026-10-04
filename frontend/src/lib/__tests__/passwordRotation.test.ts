import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, onPasswordChangeRequired, type Principal } from '../auth'
import { changePassword, listGraphs, login } from '../apiClient'
import zhCommon from '../../locales/zh-CN/common.json'

/**
 * 打包 AV（docs/95）U1147：前端把"该改密"读成**一个信号**，而不是一条文案。
 *
 * 后端 U1142/U1143 已经证明门在哪、谁被挡；这批只管三件事：① 信号发得出（登录响应与
 * 任一业务 403 两条来源）；② 文案按 code 走 i18n，英文态不露中文原文（A-4 的教训）；
 * ③ 强制位不落 localStorage——落盘就多一个会说谎的过期副本。
 */

const principal: Principal = {
  tenant_id: 't1',
  tenant_name: '演示企业 A',
  username: 'av-a',
  display_name: 'AV',
  role: 'admin',
}

const BACKEND_MESSAGE = '该账号仍在使用部署时下发的引导口令，请先修改密码后再使用平台'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** 数一次信号：订阅表就是判据——它在 node 里和浏览器里跑的是同一份代码。 */
function watchEvent() {
  let hits = 0
  const stop = onPasswordChangeRequired(() => {
    hits += 1
  })
  return { count: () => hits, stop }
}

beforeEach(() => {
  // 测试跑在 node 环境（本仓无 jsdom 依赖），localStorage 要自己给——auth.test.ts 同一形状。
  const items = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => (items.has(key) ? items.get(key)! : null),
    setItem: (key: string, value: string) => items.set(key, value),
    removeItem: (key: string) => items.delete(key),
    clear: () => items.clear(),
  })
  clearSession()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('打包 AV 首登强制改密的前端信号（docs/95 U1147）', () => {
  it('登录响应带强制位 ⇒ 发信号', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(200, { token: 'sess-1', principal, mustChangePassword: true }),
      ),
    )
    const seen = watchEvent()
    try {
      const session = await login('av-a', 'Bootstrap-1')
      expect(session.mustChangePassword).toBe(true)
      expect(seen.count()).toBe(1)
    } finally {
      seen.stop()
    }
  })

  it('业务端点的 403 ⇒ 发信号，且文案走 i18n、不露后端原文', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(403, { detail: { code: 'AUTH_PASSWORD_CHANGE_REQUIRED', message: BACKEND_MESSAGE } }),
      ),
    )
    const seen = watchEvent()
    let message: string | null = null
    try {
      await listGraphs()
    } catch (exc) {
      message = exc instanceof Error ? exc.message : String(exc)
    } finally {
      seen.stop()
    }
    expect(message, '业务请求必须 reject，否则这条守护是空的').not.toBeNull()
    expect(seen.count()).toBe(1)
    expect(message).toBe(zhCommon.error.auth.passwordChangeRequired)
    expect(message).not.toContain('引导口令')
  })

  it('判别对照：普通越权 403 不发信号', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(403, { detail: { code: 'AUTH_FORBIDDEN', message: '当前角色无权执行此操作' } }),
      ),
    )
    const seen = watchEvent()
    try {
      await expect(listGraphs()).rejects.toThrow()
      expect(seen.count()).toBe(0)
    } finally {
      seen.stop()
    }
  })

  it('这张框的出口就是改密端点本身（POST /api/auth/change-password）', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { changed: true }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(changePassword('Bootstrap-1', 'Changedpass-3')).resolves.toEqual({
      changed: true,
    })
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/auth/change-password')
    expect(init.method).toBe('POST')
  })

  it('强制位不落盘：localStorage 里不许有一个会说谎的过期副本', async () => {
    const stored: Record<string, string> = {}
    vi.stubGlobal('localStorage', {
      setItem: (key: string, value: string) => {
        stored[key] = value
      },
      getItem: (key: string) => stored[key] ?? null,
      removeItem: (key: string) => {
        delete stored[key]
      },
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(200, { token: 'sess-1', principal, mustChangePassword: true }),
      ),
    )
    const seen = watchEvent()
    try {
      await login('av-a', 'Bootstrap-1')
      expect(Object.keys(stored)).toEqual(['atlas.principal'])
      expect(stored['atlas.principal']).not.toContain('mustChangePassword')
    } finally {
      seen.stop()
    }
  })
})
