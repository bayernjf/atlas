import { afterEach, describe, expect, it } from 'vitest'
import { changeLanguage, getLanguage, t, DEFAULT_LOCALE } from '../index'

/** Login.tsx / UserBadge.tsx 实际接线的全部 key（M12 样板范围）。 */
const WIRED_KEYS = [
  'role.viewer',
  'role.operator',
  'role.admin',
  'auth.login.title',
  'auth.login.subtitle',
  'auth.login.username',
  'auth.login.usernameRequired',
  'auth.login.password',
  'auth.login.passwordRequired',
  'auth.login.submit',
  'auth.login.failed',
  'auth.login.seedHintTitle',
  'auth.login.seed.tenantA',
  'auth.login.seed.tenantB',
  'auth.session.logout',
]

/** 纯插值模板：中文来自插值变量，单独验证渲染结果。 */
const TEMPLATE_KEYS = ['auth.login.seed.accountLine']

/** Dashboard.tsx 在 dashboard namespace 下接线的页面专属 key（M12 续批）。 */
const DASHBOARD_KEYS = ['demo.title', 'demo.description', 'demo.cards.graph', 'demo.cards.loop', 'demo.cards.harness']

/** Dashboard 经 common: 前缀取用的跨页通用 key（品牌名/主导航）。 */
const SHARED_NAV_KEYS = ['common:brand.appName', 'common:nav.openEditor', 'common:nav.monitoring', 'common:nav.memory']

const hasChinese = (s: string): boolean => /[\u4e00-\u9fff]/.test(s)

afterEach(() => {
  changeLanguage(DEFAULT_LOCALE)
})

describe('zero-dependency i18n skeleton (docs/17 §2.3, M12)', () => {
  it('defaults to zh-CN', () => {
    expect(getLanguage()).toBe('zh-CN')
  })

  it('resolves nested dotted keys', () => {
    expect(t('auth.login.submit')).toBe('登录')
    expect(t('role.admin')).toBe('管理员')
  })

  it('interpolates {{var}} placeholders', () => {
    expect(
      t('auth.login.seed.accountLine', { user: 'admin-a', pass: 'admin123', role: '管理员' }),
    ).toBe('admin-a / admin123（管理员）')
  })

  it('keeps an unknown interpolation placeholder as-is', () => {
    const out = t('auth.login.seed.accountLine', { user: 'admin-a', pass: 'admin123' })
    expect(out).toContain('{{role}}')
  })

  it('returns the key itself when missing (i18next behavior)', () => {
    expect(t('does.not.exist')).toBe('does.not.exist')
  })

  it('uses defaultValue when the key is missing', () => {
    expect(t('nope.x', { defaultValue: '兜底' })).toBe('兜底')
  })

  it('falls back from an empty namespace to common for the same key', () => {
    // editor.json is an empty {} placeholder; common holds auth.login.submit
    expect(t('editor:auth.login.submit')).toBe('登录')
  })

  it('returns the key for an unknown namespace prefix (i18next behavior)', () => {
    expect(t('wat:role.viewer')).toBe('wat:role.viewer')
  })

  it('falls back to zh-CN text when switched to an empty en-US skeleton', () => {
    changeLanguage('en-US')
    expect(getLanguage()).toBe('en-US')
    // en-US/common.json is {} — must surface Chinese, never the raw key
    expect(t('auth.login.submit')).toBe('登录')
    changeLanguage('zh-CN')
    expect(t('auth.login.submit')).toBe('登录')
  })

  it('ignores an unsupported language in changeLanguage', () => {
    changeLanguage('fr-FR' as never)
    expect(getLanguage()).toBe('zh-CN')
  })

  it('every wired key resolves to a non-empty Chinese string in zh-CN', () => {
    for (const key of WIRED_KEYS) {
      const value = t(key)
      expect(typeof value).toBe('string')
      expect(value.length).toBeGreaterThan(0)
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('interpolation templates render Chinese once a role variable is supplied', () => {
    const rendered = t('auth.login.seed.accountLine', {
      user: 'admin-a',
      pass: 'admin123',
      role: t('role.admin'),
    })
    expect(rendered).toBe('admin-a / admin123（管理员）')
    expect(hasChinese(rendered)).toBe(true)
  })

  it('every wired key still resolves under the empty en-US skeleton (never raw key)', () => {
    changeLanguage('en-US')
    for (const key of [...WIRED_KEYS, ...TEMPLATE_KEYS]) {
      expect(t(key)).not.toBe(key)
    }
  })

  it('resolves dashboard namespace copy for the home page (second wired namespace)', () => {
    for (const key of DASHBOARD_KEYS) {
      const value = t(key, { ns: 'dashboard' })
      expect(value).not.toBe(key)
      expect(value.length).toBeGreaterThan(0)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
    expect(t('demo.title', { ns: 'dashboard' })).toContain('电商退款自动化')
  })

  it('reaches shared brand/nav copy via the common: prefix from a dashboard hook', () => {
    // Mirrors Dashboard.tsx: useTranslation('dashboard') then t('common:...')
    expect(t('common:brand.appName', { ns: 'dashboard' })).toBe('Atlas 运营体编排平台')
    expect(t('common:nav.openEditor', { ns: 'dashboard' })).toBe('打开流程编辑器')
    for (const key of SHARED_NAV_KEYS) {
      const value = t(key, { ns: 'dashboard' })
      expect(value).not.toBe(key)
      expect(hasChinese(value), `${key} should carry Chinese copy`).toBe(true)
    }
  })

  it('falls back to zh-CN for dashboard copy under the empty en-US skeleton', () => {
    changeLanguage('en-US')
    // en-US/dashboard.json stays {} (no translation yet); Chinese must still render
    for (const key of DASHBOARD_KEYS) {
      expect(t(key, { ns: 'dashboard' })).not.toBe(key)
    }
    for (const key of SHARED_NAV_KEYS) {
      expect(t(key, { ns: 'dashboard' })).not.toBe(key)
    }
  })
})
