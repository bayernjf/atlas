/**
 * 打包 BN（docs/130）U1436：圈会话卡的 SSR 静态面 ＋ monitoring namespace 双语奇偶。
 * 前端无 jsdom（仅 react-dom/server），取数/展开/停止等事件面不可在 SSR 触发，
 * 由 tsc + build 兜底（同 RunReportCard 先例）；本文件锁文案面与卡片骨架。
 */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { CycleSessionsCard } from '../CycleSessionsCard'
import zhMonitoring from '../../../locales/zh-CN/monitoring.json'
import enMonitoring from '../../../locales/en-US/monitoring.json'

describe('CycleSessionsCard SSR (U1436)', () => {
  it('renders title, hint, column headers and the empty state', () => {
    const html = renderToStaticMarkup(<CycleSessionsCard canOperate />)
    expect(html).toContain(zhMonitoring.cycle.title)
    expect(html).toContain(zhMonitoring.cycle.hint)
    for (const key of ['graph', 'seq', 'state', 'tokens', 'actions'] as const) {
      expect(html, `缺列头 ${key}`).toContain(zhMonitoring.cycle.col[key])
    }
    expect(html).toContain(zhMonitoring.cycle.empty)
  })
})

describe('cycle i18n parity (U1436)', () => {
  it('zh/en cycle key sets match and English has no CJK', () => {
    const flatten = (obj: unknown, prefix = ''): string[] =>
      Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
        v && typeof v === 'object'
          ? flatten(v, prefix ? `${prefix}.${k}` : k)
          : [prefix ? `${prefix}.${k}` : k],
      )
    const zh = zhMonitoring.cycle as Record<string, unknown>
    const en = enMonitoring.cycle as Record<string, unknown>
    expect(flatten(zh).sort()).toEqual(flatten(en).sort())
    expect(/[一-鿿]/.test(JSON.stringify(en))).toBe(false)
  })

  it('covers every backend session state with a bilingual label', () => {
    const states = [
      'running', 'suspended', 'completed', 'budget_exhausted', 'stopped', 'failed',
    ]
    const zh = zhMonitoring.cycle.state as Record<string, string>
    const en = enMonitoring.cycle.state as Record<string, string>
    for (const state of states) {
      expect(zh[state], `zh 缺 ${state}`).toBeTruthy()
      expect(en[state], `en 缺 ${state}`).toBeTruthy()
    }
  })
})
