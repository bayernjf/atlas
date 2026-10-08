/**
 * 打包 ZT（docs/103）U1231/U1232（渲染面）/U1236：RuleConfigEditor SSR 静态渲染。
 * 前端无 jsdom/testing-library（仅 react-dom/server，先例 DemoResetButton.test.tsx），
 * 开关切换/增删行/onChange 不可变更新等事件面无法在 SSR 触发：
 * 纯逻辑由 lib/__tests__/ruleConfig.test.ts 覆盖，事件与布局面由 tsc + build 兜底。
 */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
// U1234/U1236 机检读源码用 Vite `?raw` 导入（U1108 先例；前端 tsconfig types 只有 vite/client）
import marketSource from '../AlertRuleTemplateMarket.tsx?raw'
import monitoringSource from '../../../pages/Monitoring.tsx?raw'
import zhMonitoring from '../../../locales/zh-CN/monitoring.json'
import { RuleConfigEditor } from '../RuleConfigEditor'
import { AlertRuleTemplateMarket } from '../AlertRuleTemplateMarket'
import { defaultRuleConfig } from '../../../lib/ruleConfig'
import type { RuleConfig } from '../../../lib/apiClient'

const noop = () => {}

describe('RuleConfigEditor SSR (U1231)', () => {
  it('renders all four built-in rule labels and escalation/recovery groups', () => {
    const html = renderToStaticMarkup(
      <RuleConfigEditor value={defaultRuleConfig()} onChange={noop} />
    )
    expect(html).toContain(zhMonitoring.builtinRule.runError)
    expect(html).toContain(zhMonitoring.builtinRule.nodeFailed)
    expect(html).toContain(zhMonitoring.builtinRule.consecutiveFailures)
    expect(html).toContain(zhMonitoring.ruleForm.failureRate)
    expect(html).toContain(zhMonitoring.ruleForm.escalationLabel)
    expect(html).toContain(zhMonitoring.ruleForm.recoveryStreakLabel)
    expect(html).toContain(zhMonitoring.ruleForm.cooldownLabel)
  })

  it('renders the custom-rule section with an add button', () => {
    const html = renderToStaticMarkup(
      <RuleConfigEditor value={defaultRuleConfig()} onChange={noop} />
    )
    expect(html).toContain(zhMonitoring.custom.title)
    expect(html).toContain(zhMonitoring.custom.add)
  })

  it('renders default numeric thresholds from the value', () => {
    const html = renderToStaticMarkup(
      <RuleConfigEditor value={defaultRuleConfig()} onChange={noop} />
    )
    // antd InputNumber SSR 把值写进 input value
    expect(html).toContain('value="3"')
    expect(html).toContain('value="20"')
    expect(html).toContain('value="0.50"')
  })
})

describe('RuleConfigEditor custom rows SSR (U1232)', () => {
  const withCustom: RuleConfig = {
    ...defaultRuleConfig(),
    custom: [
      {
        cid: 'c1',
        name: '错误率高',
        enabled: true,
        expression: '{{hasError}}',
        severity: 'critical',
      },
    ],
  }

  it('renders a custom row with name, expression and severity label', () => {
    const html = renderToStaticMarkup(<RuleConfigEditor value={withCustom} onChange={noop} />)
    expect(html).toContain('错误率高')
    expect(html).toContain('{{hasError}}')
    expect(html).toContain(zhMonitoring.severity.critical)
    expect(html).toContain(zhMonitoring.custom.enabled)
  })

  it('marks an empty name row with error state and message', () => {
    const bad: RuleConfig = {
      ...defaultRuleConfig(),
      custom: [
        { cid: 'c1', name: '', enabled: true, expression: '{{hasError}}', severity: 'warning' },
      ],
    }
    const html = renderToStaticMarkup(<RuleConfigEditor value={bad} onChange={noop} />)
    expect(html).toContain('ant-input-status-error')
    expect(html).toContain(zhMonitoring.custom.nameEmpty)
  })

  it('marks an invalid expression row with error state', () => {
    const bad: RuleConfig = {
      ...defaultRuleConfig(),
      custom: [
        { cid: 'c1', name: '算术', enabled: true, expression: '1 + 2', severity: 'warning' },
      ],
    }
    const html = renderToStaticMarkup(<RuleConfigEditor value={bad} onChange={noop} />)
    expect(html).toContain('ant-input-status-error')
    // validateExpression 的中文报错（顶层须为布尔）
    expect(html).toContain('布尔')
  })

  it('disables every control when disabled is set', () => {
    const html = renderToStaticMarkup(
      <RuleConfigEditor value={defaultRuleConfig()} onChange={noop} disabled />
    )
    expect(html).toContain('ant-switch-disabled')
    expect(html).toContain('ant-input-number-disabled')
    // 新增按钮带 disabled 属性（antd v5 SSR 下 class 不落 ant-btn-disabled，属性在）
    expect(html).toMatch(/<button[^>]*ant-btn-sm[^>]*disabled=""/)
    expect(html).toContain(zhMonitoring.custom.add)
  })
})

describe('AlertRuleTemplateMarket SSR (U1234/U1235 render surface)', () => {
  it('renders only the market entry button while closed, for non-admins', () => {
    const html = renderToStaticMarkup(<AlertRuleTemplateMarket canAdmin={false} />)
    expect(html).toContain(zhMonitoring.templates.button)
    // 两个 Modal 初始关闭，表单不进 DOM
    expect(html).not.toContain(zhMonitoring.templates.configLabel)
  })

  it('renders the entry button for admins too (form opens on click, covered by tsc/build)', () => {
    const html = renderToStaticMarkup(<AlertRuleTemplateMarket canAdmin />)
    expect(html).toContain(zhMonitoring.templates.button)
  })
})

describe('source-level wiring guards (U1234/U1236)', () => {
  it('template modal no longer parses config from a JSON textarea', () => {
    expect(marketSource).not.toContain('JSON.parse')
    expect(marketSource).toContain('<RuleConfigEditor')
    expect(marketSource).toContain('validateRuleConfig')
  })

  it('monitoring page delegates the form to the shared editor and keeps save host-side', () => {
    expect(monitoringSource).toContain('<RuleConfigEditor')
    expect(monitoringSource).toContain('validateRuleConfig')
    // 内联表单函数已删除
    expect(monitoringSource).not.toContain('const addCustom')
    expect(monitoringSource).not.toContain('const updateCustom')
    expect(monitoringSource).not.toContain('const removeCustom')
    // 保存按钮与保存前错误文案仍在宿主
    expect(monitoringSource).toContain('saveRules')
    expect(monitoringSource).toContain("t('rules.nameEmpty'")
    expect(monitoringSource).toContain("t('rules.exprInvalid'")
  })
})
