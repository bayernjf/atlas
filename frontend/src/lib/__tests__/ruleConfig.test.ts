/**
 * 打包 ZT（docs/103）U1232（逻辑面）/U1233/U1234/U1235（校验拦截面）：
 * 规则配置默认值与结构化校验纯函数。组件交互面（开关/增删行/提交 payload）
 * 由 RuleConfigEditor.test.tsx 的 SSR 渲染 + tsc + build 兜底（前端无 jsdom/testing-library）。
 */
import { describe, expect, it } from 'vitest'
import { defaultRuleConfig, validateRuleConfig } from '../ruleConfig'
import type { CustomRuleConfig, RuleConfig } from '../apiClient'

describe('defaultRuleConfig (U1233)', () => {
  it('matches the backend default shape field by field', () => {
    const config = defaultRuleConfig()
    expect(config.run_error).toEqual({ enabled: true })
    expect(config.node_failed).toEqual({ enabled: true })
    expect(config.consecutive_failures).toEqual({ enabled: true, threshold: 3 })
    expect(config.failure_rate).toEqual({
      enabled: true,
      window: 20,
      min_samples: 5,
      rate: 0.5,
    })
    expect(config.custom).toEqual([])
    expect(config.escalation_ack_minutes).toBeNull()
    expect(config.recovery_healthy_streak).toBe(1)
    expect(config.recovery_cooldown_minutes).toBeNull()
  })

  it('returns independent copies (no shared array reference)', () => {
    expect(defaultRuleConfig()).not.toBe(defaultRuleConfig())
    expect(defaultRuleConfig().custom).not.toBe(defaultRuleConfig().custom)
  })
})

describe('validateRuleConfig (U1232/U1234/U1235)', () => {
  it('accepts the default config', () => {
    expect(validateRuleConfig(defaultRuleConfig())).toEqual([])
  })

  it('accepts a custom rule with a name and a boolean expression', () => {
    const config: RuleConfig = {
      ...defaultRuleConfig(),
      custom: [
        {
          cid: 'c1',
          name: '错误率高',
          enabled: true,
          expression: '{{hasError}}',
          severity: 'critical',
        } satisfies CustomRuleConfig,
      ],
    }
    expect(validateRuleConfig(config)).toEqual([])
  })

  it('flags an empty custom rule name with its index', () => {
    const config: RuleConfig = {
      ...defaultRuleConfig(),
      custom: [
        { cid: 'c1', name: '合法', enabled: true, expression: '{{hasError}}', severity: 'warning' },
        { cid: 'c2', name: '   ', enabled: true, expression: '{{hasError}}', severity: 'warning' },
      ],
    }
    const errors = validateRuleConfig(config)
    expect(errors).toContainEqual({ kind: 'custom-name-empty', index: 1 })
    expect(errors.some((e) => e.kind === 'custom-expression-invalid')).toBe(false)
  })

  it('flags a non-boolean expression with engine messages and its index', () => {
    const config: RuleConfig = {
      ...defaultRuleConfig(),
      custom: [
        { cid: 'c1', name: '算术', enabled: true, expression: '1 + 2', severity: 'warning' },
      ],
    }
    const errors = validateRuleConfig(config)
    expect(errors).toHaveLength(1)
    expect(errors[0].kind).toBe('custom-expression-invalid')
    if (errors[0].kind === 'custom-expression-invalid') {
      expect(errors[0].index).toBe(0)
      expect(errors[0].messages.length).toBeGreaterThan(0)
    }
  })

  it('flags an empty expression', () => {
    const config: RuleConfig = {
      ...defaultRuleConfig(),
      custom: [
        { cid: 'c1', name: '空表达式', enabled: true, expression: '   ', severity: 'warning' },
      ],
    }
    const errors = validateRuleConfig(config)
    expect(errors.some((e) => e.kind === 'custom-expression-invalid' && e.index === 0)).toBe(true)
  })

  it('aggregates errors across multiple custom rules', () => {
    const config: RuleConfig = {
      ...defaultRuleConfig(),
      custom: [
        { cid: 'c1', name: '', enabled: true, expression: '{{hasError}}', severity: 'warning' },
        { cid: 'c2', name: '坏表达式', enabled: true, expression: '1 + 2', severity: 'warning' },
        { cid: 'c3', name: '合法', enabled: false, expression: '{{failedCount}} > 1', severity: 'critical' },
      ],
    }
    const errors = validateRuleConfig(config)
    expect(errors).toContainEqual({ kind: 'custom-name-empty', index: 0 })
    expect(errors.some((e) => e.kind === 'custom-expression-invalid' && e.index === 1)).toBe(true)
    // 第三条合法，不出现在任何错误里
    expect(errors.every((e) => e.index !== 2)).toBe(true)
  })

  it('treats a missing custom array as valid', () => {
    const { custom: _omit, ...withoutCustom } = defaultRuleConfig()
    expect(validateRuleConfig(withoutCustom)).toEqual([])
  })
})
