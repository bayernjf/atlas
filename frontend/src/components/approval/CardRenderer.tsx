/**
 * M8 审批卡运行态（04 §5.6 追加段 / 12 §3.11）。
 *
 * CardRenderer：把后端 render_card 的 web 投影落成「只读字段 + 表单 + 动作按钮」。
 * 表单经 FormRenderer 第三来源 source='card'（仅内置控件、扁平 object）；动作按钮
 * 按卡片 actions 渲染，提交前做必填门控，最终以 {actionId, form} 交回上层 POST
 * （服务端 map_action_output 是 decision/comment 的唯一权威）。
 *
 * ApprovalCardGate：按 token 拉取 web 卡片；拉取失败（无卡 / 渲染错误 / 网络）
 * 降级为 summary + 默认同意/拒绝，保证审批通道不因卡片不可用而阻塞。
 */
import { useEffect, useState, type ReactElement } from 'react'
import { Alert, Button, Typography } from 'antd'
import { FormRenderer } from '../../lib/forms/FormRenderer'
import {
  cardFormDefaults,
  cardFormSchema,
  cardFormUiSchema,
  missingCardRequired,
} from '../../lib/cards'
import {
  getApprovalCard,
  type ApprovalRequest,
  type WebCardView,
} from '../../lib/apiClient'
import { useTranslation } from '../../locales'

const { Text } = Typography

export function CardRenderer({
  card,
  busy,
  onDecide,
}: {
  card: WebCardView
  busy?: boolean
  onDecide: (actionId: string, form: Record<string, string>) => void | Promise<void>
}): ReactElement {
  const { t, i18n } = useTranslation('approvals')
  const [values, setValues] = useState<Record<string, string>>(() => cardFormDefaults(card.form))
  const [formError, setFormError] = useState('')

  async function handleAction(actionId: string): Promise<void> {
    const missing = missingCardRequired(card.form, values)
    if (missing.length > 0) {
      const separator = i18n.language === 'en-US' ? ', ' : '、'
      const fields = missing.map((field) => field.label ?? field.name).join(separator)
      setFormError(t('card.requiredMissing', { fields }))
      return
    }
    setFormError('')
    await onDecide(actionId, values)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, width: '100%' }}>
      <div className="approval-card-fields">
        {card.fields.map((field) => (
          <div key={field.label} style={{ marginBottom: 6 }}>
            <Text type="secondary">{field.label}</Text>
            <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {String(field.value ?? '')}
            </div>
          </div>
        ))}
      </div>

      {card.form.length > 0 && (
        <FormRenderer
          schema={cardFormSchema(card.form)}
          uiSchema={cardFormUiSchema(card.form)}
          value={values}
          source="card"
          onChange={(next) => setValues(next as Record<string, string>)}
        />
      )}

      {formError && <Alert type="error" showIcon title={formError} />}

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        {card.actions.map((action) => (
          <Button
            key={action.id}
            loading={busy}
            danger={action.style === 'danger'}
            type={action.style === 'primary' ? 'primary' : 'default'}
            onClick={() => void handleAction(action.id)}
          >
            {action.label}
          </Button>
        ))}
      </div>
    </div>
  )
}

export function ApprovalCardGate({
  approval,
  busy,
  onCardDecided,
  onLegacyDecided,
}: {
  approval: ApprovalRequest & { nodeId: string }
  busy?: boolean
  onCardDecided: (actionId: string, form: Record<string, string>) => void | Promise<void>
  onLegacyDecided: (decision: 'approved' | 'rejected') => void | Promise<void>
}): ReactElement {
  const { t } = useTranslation('approvals')
  const [card, setCard] = useState<WebCardView | null>(null)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    // 不在此同步 setState：组件以 token 为 key 重挂载（Editor 传 key），state 初值即加载态。
    getApprovalCard(approval.token, 'web')
      .then((rendered) => {
        if (cancelled) return
        if (rendered.channel === 'web') setCard(rendered)
        else setLoadError(t('card.channelError'))
      })
      .catch((error: Error) => {
        if (!cancelled) setLoadError(error.message)
      })
    return () => {
      cancelled = true
    }
  }, [approval.token, t])

  if (card) {
    return <CardRenderer card={card} busy={busy} onDecide={onCardDecided} />
  }

  if (loadError) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%' }}>
        <div>
          <Text type="secondary">{t('card.summaryLabel')}</Text>
          <div>{approval.summary}</div>
        </div>
        <Alert
          type="warning"
          showIcon
          title={t('card.degradedWarning', { error: loadError })}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <Button danger loading={busy} onClick={() => void onLegacyDecided('rejected')}>
            {t('actions.reject')}
          </Button>
          <Button type="primary" loading={busy} onClick={() => void onLegacyDecided('approved')}>
            {t('actions.approve')}
          </Button>
        </div>
      </div>
    )
  }

  return <Text type="secondary">{t('card.loading')}</Text>
}
