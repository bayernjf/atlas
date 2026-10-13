/**
 * 打包 AU（docs/129）U1421：Usage 页 SSR 静态渲染与 i18n parity。
 * 前端无 jsdom/testing-library（仅 react-dom/server）：取数、分组切换、刷新等事件面
 * 无法在 SSR 触发，由 tsc + build 兜底（同 RunReportCard 先例）；本文件锁定
 * 静态文案面（页头、说明、三档分组、两张表的表头）与 usage 命名空间双语。
 */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Usage } from '../Usage'
import type { Principal } from '../../lib/auth'
import zhUsage from '../../locales/zh-CN/usage.json'
import enUsage from '../../locales/en-US/usage.json'

const principal = {
  tenant_id: 't1',
  tenant_name: '默认租户',
  username: 'admin-a',
  role: 'admin',
  display_name: '管理员',
} as Principal

describe('Usage page SSR (U1421)', () => {
  it('renders title, notice, three group options and both table headers', () => {
    const html = renderToStaticMarkup(
      <Usage principal={principal} onLogout={() => {}} onBack={() => {}} />,
    )
    // 页头与说明
    expect(html).toContain(zhUsage.title)
    expect(html).toContain(zhUsage.notice.title)
    expect(html).toContain(zhUsage.notice.description)
    // 三档分组
    expect(html).toContain(zhUsage.summary.groupCaller)
    expect(html).toContain(zhUsage.summary.groupModel)
    expect(html).toContain(zhUsage.summary.groupDay)
    // 聚合表表头
    expect(html).toContain(zhUsage.summary.key)
    expect(html).toContain(zhUsage.summary.calls)
    expect(html).toContain(zhUsage.summary.promptTokens)
    expect(html).toContain(zhUsage.summary.completionTokens)
    expect(html).toContain(zhUsage.summary.totalTokens)
    expect(html).toContain(zhUsage.summary.errors)
    // 明细表表头与脚注
    expect(html).toContain(zhUsage.recent.title)
    expect(html).toContain(zhUsage.recent.ts)
    expect(html).toContain(zhUsage.recent.caller)
    expect(html).toContain(zhUsage.recent.model)
    expect(html).toContain(zhUsage.recent.latency)
    expect(html).toContain(zhUsage.recent.hint)
  })

  it('renders empty states instead of a bare table', () => {
    const html = renderToStaticMarkup(
      <Usage principal={principal} onLogout={() => {}} onBack={() => {}} />,
    )
    expect(html).toContain(zhUsage.summary.empty)
    expect(html).toContain(zhUsage.recent.empty)
  })
})

describe('Usage i18n parity (U1421)', () => {
  it('zh/en usage namespace key sets match', () => {
    const flatten = (obj: unknown, prefix = ''): string[] =>
      Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
        v && typeof v === 'object'
          ? flatten(v, prefix ? `${prefix}.${k}` : k)
          : [prefix ? `${prefix}.${k}` : k],
      )
    expect(flatten(zhUsage).sort()).toEqual(flatten(enUsage).sort())
  })

  it('en usage catalog has no CJK characters', () => {
    expect(/[一-鿿]/.test(JSON.stringify(enUsage))).toBe(false)
  })

  it('every backend caller identity has a bilingual label', () => {
    const callers = [
      'ai_decision',
      'condition_llm',
      'intent_recognition',
      'info_extraction',
      'content_generation',
      'memory_auto_extract',
      'nl_generate',
      'reflection_summarize',
    ]
    const zh = zhUsage.caller as Record<string, string>
    const en = enUsage.caller as Record<string, string>
    for (const caller of callers) {
      expect(zh[caller], `zh 缺 ${caller}`).toBeTruthy()
      expect(en[caller], `en 缺 ${caller}`).toBeTruthy()
    }
  })
})
