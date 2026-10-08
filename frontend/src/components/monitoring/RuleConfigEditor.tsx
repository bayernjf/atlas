import { Button, Input, InputNumber, Select, Space, Switch, Typography } from 'antd'
import { useTranslation } from '../../locales'
import { validateRuleConfig } from '../../lib/ruleConfig'
import type { CustomRuleConfig, RuleConfig } from '../../lib/apiClient'

/**
 * 规则配置表单（docs/103 打包 ZT）：Monitoring 生效规则卡片与规则模板市场
 * 新建/编辑 Modal 共用的纯受控编辑器。不发请求、不碰 antd Form、不持有最终态。
 * 默认值与结构化校验在 lib/ruleConfig（oxlint only-export-components 约束）。
 */
export function RuleConfigEditor(props: {
  value: RuleConfig
  onChange: (next: RuleConfig) => void
  disabled?: boolean
}) {
  const { value: rules, onChange: setRules, disabled } = props
  const { t } = useTranslation('monitoring')

  // 行内红框与宿主提交校验同源（lib/ruleConfig.validateRuleConfig）
  const validationErrors = validateRuleConfig(rules)
  const updateCustom = (idx: number, patch: Partial<CustomRuleConfig>) => {
    setRules({
      ...rules,
      custom: (rules.custom ?? []).map((rule, i) => (i === idx ? { ...rule, ...patch } : rule)),
    })
  }
  const addCustom = () => {
    setRules({
      ...rules,
      custom: [
        ...(rules.custom ?? []),
        {
          cid: crypto.randomUUID(),
          name: '',
          enabled: true,
          expression: '{{hasError}}',
          severity: 'warning' as const,
        },
      ],
    })
  }
  const removeCustom = (idx: number) => {
    setRules({ ...rules, custom: (rules.custom ?? []).filter((_, i) => i !== idx) })
  }

  return (
    <>
      <Space wrap size="large">
        <Space>
          <span>{t('builtinRule.runError')}</span>
          <Switch
            disabled={disabled}
            checked={rules.run_error.enabled}
            onChange={(enabled) => setRules({ ...rules, run_error: { enabled } })}
          />
        </Space>
        <Space>
          <span>{t('builtinRule.nodeFailed')}</span>
          <Switch
            disabled={disabled}
            checked={rules.node_failed.enabled}
            onChange={(enabled) => setRules({ ...rules, node_failed: { enabled } })}
          />
        </Space>
        <Space>
          <span>{t('builtinRule.consecutiveFailures')}</span>
          <Switch
            disabled={disabled}
            checked={rules.consecutive_failures.enabled}
            onChange={(enabled) =>
              setRules({
                ...rules,
                consecutive_failures: { ...rules.consecutive_failures, enabled },
              })
            }
          />
          <span>{t('ruleForm.threshold')}</span>
          <InputNumber
            disabled={disabled}
            min={1}
            max={200}
            value={rules.consecutive_failures.threshold}
            onChange={(value) =>
              value !== null &&
              setRules({
                ...rules,
                consecutive_failures: { ...rules.consecutive_failures, threshold: value },
              })
            }
            suffix={t('ruleForm.unitTimes')}
          />
        </Space>
        <Space>
          <span>{t('ruleForm.failureRate')}</span>
          <Switch
            disabled={disabled}
            checked={rules.failure_rate.enabled}
            onChange={(enabled) =>
              setRules({
                ...rules,
                failure_rate: { ...rules.failure_rate, enabled },
              })
            }
          />
          <span>{t('ruleForm.window')}</span>
          <InputNumber
            disabled={disabled}
            min={1}
            max={200}
            value={rules.failure_rate.window}
            onChange={(value) =>
              value !== null &&
              setRules({
                ...rules,
                failure_rate: { ...rules.failure_rate, window: value },
              })
            }
            suffix={t('ruleForm.unitTimes')}
          />
          <span>{t('ruleForm.minSamples')}</span>
          <InputNumber
            disabled={disabled}
            min={1}
            max={200}
            value={rules.failure_rate.min_samples}
            onChange={(value) =>
              value !== null &&
              setRules({
                ...rules,
                failure_rate: { ...rules.failure_rate, min_samples: value },
              })
            }
          />
          <span>{t('ruleForm.rate')}</span>
          <InputNumber
            disabled={disabled}
            min={0}
            max={1}
            step={0.05}
            value={rules.failure_rate.rate}
            onChange={(value) =>
              value !== null &&
              setRules({
                ...rules,
                failure_rate: { ...rules.failure_rate, rate: value },
              })
            }
          />
        </Space>
        <Space wrap style={{ marginTop: 12 }}>
          <span>{t('ruleForm.escalationLabel')}</span>
          <Switch
            disabled={disabled}
            checked={rules.escalation_ack_minutes != null}
            onChange={(enabled) =>
              setRules({ ...rules, escalation_ack_minutes: enabled ? 30 : null })
            }
          />
          {rules.escalation_ack_minutes != null && (
            <>
              <InputNumber
                disabled={disabled}
                min={1}
                max={10080}
                value={rules.escalation_ack_minutes}
                onChange={(value) =>
                  value !== null && setRules({ ...rules, escalation_ack_minutes: value })
                }
              />
              <span>{t('ruleForm.escalationUnit')}</span>
            </>
          )}
        </Space>
        <Space wrap style={{ marginTop: 12 }}>
          <span>{t('ruleForm.recoveryStreakLabel')}</span>
          <InputNumber
            disabled={disabled}
            min={1}
            max={20}
            value={rules.recovery_healthy_streak ?? 1}
            onChange={(value) =>
              value !== null && setRules({ ...rules, recovery_healthy_streak: value })
            }
          />
          <span>{t('ruleForm.recoveryStreakUnit')}</span>
        </Space>
        <Space wrap style={{ marginTop: 12 }}>
          <span>{t('ruleForm.cooldownLabel')}</span>
          <Switch
            disabled={disabled}
            checked={rules.recovery_cooldown_minutes != null}
            onChange={(enabled) =>
              setRules({ ...rules, recovery_cooldown_minutes: enabled ? 30 : null })
            }
          />
          {rules.recovery_cooldown_minutes != null && (
            <>
              <InputNumber
                disabled={disabled}
                min={1}
                max={10080}
                value={rules.recovery_cooldown_minutes}
                onChange={(value) =>
                  value !== null && setRules({ ...rules, recovery_cooldown_minutes: value })
                }
              />
              <span>{t('ruleForm.cooldownUnit')}</span>
            </>
          )}
        </Space>
      </Space>

      <div style={{ marginTop: 16 }}>
        <Typography.Text strong>{t('custom.title')}</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8, marginTop: 4 }}>
          {/* custom.hint 内含教学用字面 {{status}}/{{hasError}}，t() 不传这两个变量故原样保留 */}
          {t('custom.hint')}
        </Typography.Paragraph>
        {(rules.custom ?? []).map((rule, idx) => {
          const nameEmpty = validationErrors.some(
            (error) => error.index === idx && error.kind === 'custom-name-empty',
          )
          const exprError = validationErrors.find(
            (error) => error.index === idx && error.kind === 'custom-expression-invalid',
          )
          const exprMessages = exprError && 'messages' in exprError ? exprError.messages : []
          return (
            <Space
              key={rule.cid}
              wrap
              align="start"
              style={{ display: 'flex', marginBottom: 8 }}
            >
              <Input
                disabled={disabled}
                placeholder={t('custom.namePlaceholder')}
                value={rule.name}
                style={{ width: 150 }}
                status={nameEmpty ? 'error' : undefined}
                onChange={(event) => updateCustom(idx, { name: event.target.value })}
              />
              <Input
                disabled={disabled}
                placeholder="{{status}} == 'error' || {{hasError}}"
                value={rule.expression}
                style={{ width: 340, fontFamily: 'monospace' }}
                status={exprMessages.length > 0 ? 'error' : undefined}
                onChange={(event) => updateCustom(idx, { expression: event.target.value })}
              />
              <Select
                disabled={disabled}
                value={rule.severity}
                style={{ width: 100 }}
                onChange={(severity) => updateCustom(idx, { severity })}
                options={[
                  { value: 'warning', label: t('severity.warning') },
                  { value: 'critical', label: t('severity.critical') },
                ]}
              />
              <Space style={{ marginTop: 4 }}>
                <span>{t('custom.enabled')}</span>
                <Switch
                  disabled={disabled}
                  checked={rule.enabled}
                  onChange={(enabled) => updateCustom(idx, { enabled })}
                />
              </Space>
              <Button
                disabled={disabled}
                danger
                size="small"
                style={{ marginTop: 2 }}
                onClick={() => removeCustom(idx)}
              >
                {t('common:button.delete')}
              </Button>
              {(nameEmpty || exprMessages.length > 0) && (
                <Typography.Text type="danger" style={{ marginTop: 6 }}>
                  {nameEmpty ? t('custom.nameEmpty') : exprMessages.join('；')}
                </Typography.Text>
              )}
            </Space>
          )
        })}
        <Button disabled={disabled} size="small" onClick={addCustom}>
          {t('custom.add')}
        </Button>
      </div>
    </>
  )
}
