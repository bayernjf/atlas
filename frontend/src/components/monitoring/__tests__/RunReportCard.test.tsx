/**
 * 打包 AG（docs/113）U1315/U1316：RunReportCard SSR 静态渲染与 i18n parity。
 * 前端无 jsdom/testing-library（仅 react-dom/server）：fetch 取数、分组切换、days 变更、
 * window.open 导出等事件面无法在 SSR 触发，由 tsc + build 兜底；本文件锁定静态渲染与双语键。
 */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RunReportCard } from '../RunReportCard'
import zhMonitoring from '../../../locales/zh-CN/monitoring.json'
import enMonitoring from '../../../locales/en-US/monitoring.json'

describe('RunReportCard SSR (U1315)', () => {
  it('renders title, group switch, days selector, table headers and export buttons', () => {
    const html = renderToStaticMarkup(<RunReportCard />)
    expect(html).toContain(zhMonitoring.runReport.card.title)
    expect(html).toContain(zhMonitoring.runReport.group.day)
    expect(html).toContain(zhMonitoring.runReport.group.version)
    expect(html).toContain(zhMonitoring.runReport.col.total)
    expect(html).toContain(zhMonitoring.runReport.col.successRate)
    expect(html).toContain(zhMonitoring.runReport.col.p95)
    expect(html).toContain(zhMonitoring.runReport.export.csv)
    expect(html).toContain(zhMonitoring.runReport.export.json)
  })
})

describe('RunReportCard i18n parity (U1316)', () => {
  it('zh/en runReport nested keys match', () => {
    const zh = zhMonitoring.runReport
    const en = enMonitoring.runReport
    expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort())
    expect(Object.keys(zh.col).sort()).toEqual(Object.keys(en.col).sort())
    expect(Object.keys(zh.group).sort()).toEqual(Object.keys(en.group).sort())
    expect(Object.keys(zh.export).sort()).toEqual(Object.keys(en.export).sort())
  })

  it('en runReport has no CJK characters', () => {
    expect(/[\u4e00-\u9fff]/.test(JSON.stringify(enMonitoring.runReport))).toBe(false)
  })
})
