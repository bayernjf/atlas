import { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Select, Space, Switch, message } from 'antd'
import { useTranslation } from '../locales'
import {
  getWebhookSubscriptions,
  putWebhookSubscriptions,
  type ChannelBindingView,
  type SavedGraphSummary,
  type WebhookSubscription,
} from '../lib/apiClient'

const WEBHOOK_TOPICS = ['orders/create', 'orders/updated', 'refunds/create']

type WebhookSubscriptionsModalProps = {
  open: boolean
  binding: ChannelBindingView
  graphs: SavedGraphSummary[]
  onClose: () => void
}

export function WebhookSubscriptionsModal({
  open, binding, graphs, onClose,
}: WebhookSubscriptionsModalProps) {
  const { t } = useTranslation('channels')
  const [subs, setSubs] = useState<WebhookSubscription[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const publicUrl = `${window.location.origin}/api/channels/hooks/shopify/${binding.id}`

  useEffect(() => {
    if (!open) return
    let active = true
    setLoading(true)
    setError('')
    getWebhookSubscriptions(binding.id)
      .then((items) => {
        if (active) setSubs(items)
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : t('webhook.error.load'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [open, binding.id, t])

  const patch = (index: number, values: Partial<WebhookSubscription>) =>
    setSubs((prev) =>
      prev.map((sub, i) => (i === index ? { ...sub, ...values } : sub)),
    )

  const remove = (index: number) =>
    setSubs((prev) => prev.filter((_, i) => i !== index))

  const add = () =>
    setSubs((prev) => [
      ...prev,
      { topic: WEBHOOK_TOPICS[0], graphId: graphs[0]?.id ?? '', enabled: true },
    ])

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(publicUrl)
      message.success(t('webhook.message.urlCopied'))
    } catch {
      message.error(t('webhook.error.copy'))
    }
  }

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const items = await putWebhookSubscriptions(binding.id, subs)
      setSubs(items)
      message.success(t('webhook.message.saved'))
    } catch (err) {
      setError(err instanceof Error ? err.message : t('webhook.error.save'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={t('webhook.modal.title')}
      open={open}
      onCancel={onClose}
      width={640}
      destroyOnClose
      footer={[
        <Button key="cancel" onClick={onClose}>
          {t('common:button.cancel')}
        </Button>,
        <Button key="save" type="primary" loading={saving} onClick={() => void save()}>
          {t('common:button.save')}
        </Button>,
      ]}
    >
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
      <Space direction="vertical" size={12} style={{ display: 'flex', marginTop: 12 }}>
        <div>
          <div style={{ fontSize: 12, color: 'var(--ant-color-text-secondary)', marginBottom: 4 }}>
            {t('webhook.urlLabel')}
          </div>
          <Space.Compact style={{ width: '100%' }}>
            <Input readOnly value={publicUrl} />
            <Button onClick={() => void copyUrl()}>{t('webhook.copy')}</Button>
          </Space.Compact>
        </div>
        <Alert type="info" showIcon message={t('webhook.hint')} />
        <div>
          <div style={{ marginBottom: 8 }}>{t('webhook.subsTitle')}</div>
          <Space direction="vertical" size={8} style={{ display: 'flex' }}>
            {subs.map((sub, index) => (
              <Space key={index} align="center">
                <Select
                  value={sub.topic}
                  style={{ width: 170 }}
                  options={WEBHOOK_TOPICS.map((topic) => ({
                    value: topic,
                    label: t(`webhook.topic.${topic.replace('/', '_')}`),
                  }))}
                  onChange={(topic: string) => patch(index, { topic })}
                />
                <Select
                  value={sub.graphId || undefined}
                  style={{ width: 200 }}
                  placeholder={t('webhook.graphPlaceholder')}
                  options={graphs.map((graph) => ({
                    value: graph.id,
                    label: `${graph.id}（${graph.node_count} ${t('webhook.nodesUnit')}）`,
                  }))}
                  onChange={(graphId: string) => patch(index, { graphId })}
                />
                <Switch
                  checked={sub.enabled}
                  checkedChildren={t('webhook.enabled')}
                  unCheckedChildren={t('webhook.disabled')}
                  onChange={(enabled: boolean) => patch(index, { enabled })}
                />
                <Button size="small" danger onClick={() => remove(index)}>
                  {t('common:button.delete')}
                </Button>
              </Space>
            ))}
          </Space>
          <Button size="small" style={{ marginTop: 10 }} onClick={add} disabled={loading}>
            {t('webhook.add')}
          </Button>
        </div>
      </Space>
    </Modal>
  )
}
