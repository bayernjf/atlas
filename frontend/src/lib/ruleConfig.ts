import type { RuleConfig } from './apiClient'
import { validateExpression } from './conditions'

/**
 * 规则配置的纯函数面（docs/103 打包 ZT）：默认值与结构化校验。
 * 组件 RuleConfigEditor、Monitoring 保存、模板市场提交三处共用。
 */

export type RuleConfigError =
  | { kind: 'custom-name-empty'; index: number }
  | { kind: 'custom-expression-invalid'; index: number; messages: string[] }

/** 结构化校验：表达式仍走 conditions 安全引擎（禁 eval），文案归宿主/行内渲染。 */
export function validateRuleConfig(config: RuleConfig): RuleConfigError[] {
  const errors: RuleConfigError[] = []
  ;(config.custom ?? []).forEach((rule, index) => {
    if (!rule.name.trim()) {
      errors.push({ kind: 'custom-name-empty', index })
    }
    const messages = validateExpression(rule.expression)
    if (messages.length > 0) {
      errors.push({ kind: 'custom-expression-invalid', index, messages })
    }
  })
  return errors
}

/** 新建模板的默认配置（从 AlertRuleTemplateMarket 迁入，与后端默认同形）。 */
export function defaultRuleConfig(): RuleConfig {
  return {
    run_error: { enabled: true },
    node_failed: { enabled: true },
    consecutive_failures: { enabled: true, threshold: 3 },
    failure_rate: { enabled: true, window: 20, min_samples: 5, rate: 0.5 },
    custom: [],
    escalation_ack_minutes: null,
    recovery_healthy_streak: 1,
    recovery_cooldown_minutes: null,
  }
}
