import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ROLE_LABELS,
  UNAUTHORIZED_EVENT,
  clearSession,
  getStoredPrincipal,
  getToken,
  handleUnauthorized,
  roleCan,
  saveSession,
  type Principal,
} from '../auth'

const viewer: Principal = {
  tenant_id: 't1',
  tenant_name: '演示企业 A',
  username: 'viewer-a',
  display_name: 'A 企业访客',
  role: 'viewer',
}

class MemoryStorage {
  private items = new Map<string, string>()
  getItem(key: string) {
    return this.items.has(key) ? this.items.get(key)! : null
  }
  setItem(key: string, value: string) {
    this.items.set(key, value)
  }
  removeItem(key: string) {
    this.items.delete(key)
  }
  clear() {
    this.items.clear()
  }
}

beforeEach(() => {
  vi.stubGlobal('localStorage', new MemoryStorage())
  clearSession()
})

describe('auth session storage (04 §5.14)', () => {
  it('persists and restores token + principal, then clears both', () => {
    expect(getToken()).toBeNull()
    expect(getStoredPrincipal()).toBeNull()

    saveSession('sess-abc', viewer)
    expect(getToken()).toBe('sess-abc')
    expect(getStoredPrincipal()).toEqual(viewer)

    clearSession()
    expect(getToken()).toBeNull()
    expect(getStoredPrincipal()).toBeNull()
  })

  it('returns null for corrupted principal JSON', () => {
    localStorage.setItem('atlas.session_token', 'sess-abc')
    localStorage.setItem('atlas.principal', '{not-json')
    expect(getStoredPrincipal()).toBeNull()
  })
})

describe('roleCan capability matrix', () => {
  it('viewer is read-only, operator operates, admin administers', () => {
    expect(roleCan('viewer', 'read')).toBe(true)
    expect(roleCan('viewer', 'operate')).toBe(false)
    expect(roleCan('viewer', 'administer')).toBe(false)

    expect(roleCan('operator', 'operate')).toBe(true)
    expect(roleCan('operator', 'administer')).toBe(false)

    expect(roleCan('admin', 'operate')).toBe(true)
    expect(roleCan('admin', 'administer')).toBe(true)
  })

  it('maps roles to Chinese labels', () => {
    expect(ROLE_LABELS.viewer).toBe('访客')
    expect(ROLE_LABELS.operator).toBe('运营')
    expect(ROLE_LABELS.admin).toBe('管理员')
  })
})

describe('handleUnauthorized', () => {
  it('clears the session and dispatches the atlas:unauthorized event', () => {
    saveSession('sess-abc', viewer)
    const dispatchEvent = vi.fn()
    vi.stubGlobal('window', { dispatchEvent })
    handleUnauthorized()
    expect(dispatchEvent).toHaveBeenCalledOnce()
    expect(dispatchEvent.mock.calls[0][0].type).toBe(UNAUTHORIZED_EVENT)
    expect(getToken()).toBeNull()
  })
})
