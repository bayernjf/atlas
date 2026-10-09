/**
 * 打包 AH（docs/114）U1322/U1323：ShadowRunModal SSR 不崩与流式新键 parity。
 * antd Modal 经客户端 portal 渲染，SSR 下输出为空、无法断言内部内容；前端也无
 * jsdom/testing-library（仅 react-dom/server）：发起流式、node_start/node_end 实时
 * 进度 Tag、终帧结果等事件面由 tsc + build + 双语浏览器冒烟兜底。本文件锁定 SSR 不崩与新增 i18n 键。
 */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ShadowRunModal } from '../ShadowRunModal'
import zhMonitoring from '../../../locales/zh-CN/monitoring.json'
import enMonitoring from '../../../locales/en-US/monitoring.json'

describe('ShadowRunModal SSR (U1322)', () => {
  it('renders without throwing (antd Modal content is client-portaled)', () => {
    expect(() =>
      renderToStaticMarkup(<ShadowRunModal open graphId="graph-1" onClose={() => undefined} />),
    ).not.toThrow()
  })
})

describe('ShadowRunModal streaming i18n parity (U1323)', () => {
  it('zh/en live progress keys match', () => {
    expect(zhMonitoring.shadow.modal.liveProgress).toBeTruthy()
    expect(enMonitoring.shadow.modal.liveProgress).toBeTruthy()
    expect(Object.keys(zhMonitoring.shadow.progress).sort()).toEqual(
      Object.keys(enMonitoring.shadow.progress).sort(),
    )
  })

  it('en streaming keys have no CJK characters', () => {
    const enStreaming = {
      liveProgress: enMonitoring.shadow.modal.liveProgress,
      progress: enMonitoring.shadow.progress,
    }
    expect(/[\u4e00-\u9fff]/.test(JSON.stringify(enStreaming))).toBe(false)
  })
})
