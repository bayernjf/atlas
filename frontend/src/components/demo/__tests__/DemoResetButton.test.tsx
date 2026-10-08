/**
 * docs/101 D59 §4.3：前端组件测试——按钮渲染条件与文案（SSR 静态渲染）。
 * Popconfirm 弹层在 SSR 不渲染，确认/成功文案由 i18n catalog 断言覆盖；
 * 「点击调用 reset」由 apiClient 契约测试覆盖（resetDemo POST /api/demo/reset）。
 */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import zhEditor from '../../../locales/zh-CN/editor.json'
import { DemoResetButton } from '../DemoResetButton'

describe('DemoResetButton (docs/101 D59)', () => {
  it('renders nothing when demo surface is off or role is not admin', () => {
    expect(renderToStaticMarkup(<DemoResetButton visible={false} />)).toBe('')
  })

  it('renders reset button when visible, with i18n texts present', () => {
    const html = renderToStaticMarkup(<DemoResetButton visible />)
    expect(html).toContain('重置演示数据')
    expect(html).toContain('ant-btn-dangerous')
    // 确认文案（Popconfirm title）与成功提示存在于 i18n catalog。
    expect(zhEditor.header.resetDemoConfirm).toContain('清空并重置全部演示数据')
    expect(zhEditor.header.resetDemoSuccess).toBe('演示数据已重置')
  })
})
