import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Radio,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd'
import {
  getAlertChannel,
  updateAlertChannel,
  type AlertChannelConfig,
  type AlertChannelKind,
} from '../../lib/apiClient'
import {
  ALERT_CHANNEL_OPTIONS,
  secretVisible,
  validateAlertChannelDraft,
} from '../../lib/alertChannel'
import { formatTime } from '../../lib/monitoring'
import { useTranslation } from '../../locales'

type Props = {
  canAdmin: boolean
}

function toDraft(config: AlertChannelConfig) {
  return {
    enabled: config.enabled,
    channel: config.channel,
    to: config.to,
    secret: config.secret,
    minSeverity: config.minSeverity,
  }
}

export function AlertChannelCard({ canAdmin }: Props) {
  const { t } = useTranslation('monitoring')
  const [config, setConfig] = useState<AlertChannelConfig | null>(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getAlertChannel()
      .then(setConfig)
      .catch((reason) => setError(String(reason)))
  }, [])

  if (!config) return null

  const draft = toDraft(config)
  const patch = (changes: Partial<typeof draft>) => {
    setSaved(false)
    setConfig({ ...config, ...changes })
  }

  const save = async () => {
    setError('')
    const clientErrors = validateAlertChannelDraft(draft)
    if (clientErrors.length > 0) {
      setError(clientErrors.join('；'))
      return
    }
    try {
      const updated = await updateAlertChannel(draft)
      setConfig(updated)
      setSaved(true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const delivery = config.lastDelivery
  const isEmail = draft.channel === 'email'

  return (
    <Card title={t('alertChannel.cardTitle')}>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        {t('alertChannel.subtitle')}
      </Typography.Paragraph>
      {error && (
        <Alert
          type="error"
          showIcon
          message={t('alertChannel.saveFailed')}
          description={error}
          style={{ marginBottom: 12 }}
        />
      )}
      {saved && !error && (
        <Alert type="success" showIcon message={t('alertChannel.saved')} style={{ marginBottom: 12 }} />
      )}
      <Form layout="vertical" disabled={!canAdmin}>
        <Space wrap size="large" align="start">
          <Space>
            <span>{t('alertChannel.enable')}</span>
            <Switch
              checked={draft.enabled}
              onChange={(enabled) => patch({ enabled })}
            />
          </Space>
          <Form.Item label={t('alertChannel.channel')} style={{ marginBottom: 0 }}>
            <Select<AlertChannelKind>
              value={draft.channel}
              style={{ width: 140 }}
              onChange={(channel) => patch({ channel })}
              options={ALERT_CHANNEL_OPTIONS.map((channel) => ({
                value: channel,
                label: channel,
              }))}
            />
          </Form.Item>
          <Form.Item
            label={isEmail ? t('alertChannel.targetEmail') : t('alertChannel.targetUrl')}
            style={{ marginBottom: 0 }}
          >
            <Input
              value={draft.to}
              style={{ width: 300 }}
              onChange={(event) => patch({ to: event.target.value })}
            />
          </Form.Item>
          {secretVisible(draft.channel) && (
            <Form.Item label={t('alertChannel.secret')} style={{ marginBottom: 0 }}>
              <Input.Password
                value={draft.secret}
                style={{ width: 180 }}
                onChange={(event) => patch({ secret: event.target.value })}
              />
            </Form.Item>
          )}
          <Form.Item label={t('alertChannel.minSeverity')} style={{ marginBottom: 0 }}>
            <Radio.Group
              value={draft.minSeverity}
              onChange={(event) => patch({ minSeverity: event.target.value })}
              optionType="button"
              buttonStyle="solid"
              options={[
                { value: 'critical', label: t('alertChannel.severityCritical') },
                { value: 'warning', label: t('alertChannel.severityWarning') },
              ]}
            />
          </Form.Item>
        </Space>
        <Space style={{ marginTop: 16 }}>
          {canAdmin && (
            <Button type="primary" onClick={save}>
              {t('alertChannel.save')}
            </Button>
          )}
          {!canAdmin && (
            <Typography.Text type="secondary">{t('alertChannel.readOnlyHint')}</Typography.Text>
          )}
          <span>{t('alertChannel.lastDelivery')}：</span>
          {delivery ? (
            <Space size={4}>
              <Tag color={delivery.errorCode ? 'red' : 'green'}>
                {delivery.errorCode ? t('alertChannel.deliveryFailed') : t('alertChannel.deliverySuccess')}
              </Tag>
              <Typography.Text type="secondary">{formatTime(delivery.lastNotifiedAt)}</Typography.Text>
              {delivery.errorCode && (
                <Typography.Text type="danger">
                  {delivery.errorCode}
                  {delivery.errorMessage ? `：${delivery.errorMessage}` : ''}
                </Typography.Text>
              )}
            </Space>
          ) : (
            <Typography.Text type="secondary">{t('alertChannel.never')}</Typography.Text>
          )}
        </Space>
      </Form>
    </Card>
  )
}
