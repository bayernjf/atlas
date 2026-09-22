import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createChannelBinding,
  deleteChannelBinding,
  listChannelBindings,
  listGraphs,
  testChannelBinding,
  type ChannelBindingView,
  type ConnectionView,
  type SavedGraphSummary,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { useTranslation } from '../locales'
import { WebhookSubscriptionsModal } from './WebhookSubscriptionsModal'

type ChannelBindingsCardProps = {
  principal: Principal
  connections: ConnectionView[]
}

type BindingForm = {
  provider: string
  connectionId: string
  shop: string
  apiVersion: string
}

const EMPTY_FORM: BindingForm = {
  provider: 'shopify',
  connectionId: '',
  shop: '',
  apiVersion: '2025-01',
}

export function ChannelBindingsCard({ principal, connections }: ChannelBindingsCardProps) {
  const { t } = useTranslation('channels')
  const canAdmin = roleCan(principal.role, 'administer')
  const canOperate = roleCan(principal.role, 'operate')

  const [items, setItems] = useState<ChannelBindingView[]>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')

  const [modalOpen, setModalOpen] = useState(false)
  const [form, setForm] = useState<BindingForm>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  const [graphs, setGraphs] = useState<SavedGraphSummary[]>([])
  const [webhookBinding, setWebhookBinding] = useState<ChannelBindingView | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await listChannelBindings()
      setItems(data)
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('error.loadList'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!canAdmin) return
    // eslint-disable-next-line react/set-state-in-effect -- 管理员打开 webhook 弹窗需要图列表
    listGraphs()
      .then(setGraphs)
      .catch(() => undefined)
  }, [canAdmin])

  const patchForm = (patch: Partial<BindingForm>) =>
    setForm((prev) => ({ ...prev, ...patch }))

  const handleBind = async () => {
    if (!form.connectionId || !form.shop.trim()) {
      setFormError(t('error.formInvalid'))
      return
    }
    setSaving(true)
    try {
      await createChannelBinding({
        provider: form.provider,
        connectionId: form.connectionId,
        config: { shop: form.shop.trim(), apiVersion: form.apiVersion.trim() || '2025-01' },
      })
      message.success(t('message.bound'))
      setModalOpen(false)
      await refresh()
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('error.bind'))
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async (record: ChannelBindingView) => {
    try {
      const result = await testChannelBinding(record.id)
      if (result.ok) {
        message.success(t('message.testOk'))
      } else {
        message.warning(result.reason ? `${t('message.testFail')}：${result.reason}` : t('message.testFail'))
      }
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('error.test'))
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteChannelBinding(id)
      message.success(t('message.deleted'))
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('error.delete'))
    }
  }

  const columns: ColumnsType<ChannelBindingView> = [
    {
      title: t('col.shop'),
      key: 'shop',
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <span>{record.config.shop}</span>
          <span style={{ fontSize: 12, color: 'var(--ant-color-text-secondary)' }}>
            {record.config.apiVersion}
          </span>
        </Space>
      ),
    },
    {
      title: t('col.provider'),
      dataIndex: 'provider',
      key: 'provider',
      width: 120,
      render: (provider: string) =>
        provider === 'shopify' ? t('provider.shopify') : provider,
    },
    {
      title: t('col.status'),
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: ChannelBindingView['status'], record) => (
        <Tag color={status === 'connected' ? 'green' : 'red'}>
          {status === 'connected' ? t('status.connected') : t('status.error')}
          {status === 'error' && record.lastError ? `：${record.lastError}` : ''}
        </Tag>
      ),
    },
    {
      title: t('col.connection'),
      dataIndex: 'connectionId',
      key: 'connectionId',
      width: 160,
      ellipsis: true,
    },
    {
      title: t('col.actions'),
      key: 'actions',
      width: 250,
      render: (_, record) => (
        <Space size={4} wrap>
          <Button size="small" onClick={() => handleTest(record)}>
            {t('button.test')}
          </Button>
          {canAdmin && (
            <Button size="small" onClick={() => setWebhookBinding(record)}>
              {t('webhook.button')}
            </Button>
          )}
          {canAdmin && (
            <Popconfirm
              title={t('deleteConfirm.title')}
              description={t('deleteConfirm.description')}
              okText={t('common:button.delete')}
              cancelText={t('common:button.cancel')}
              okButtonProps={{ danger: true }}
              onConfirm={() => handleDelete(record.id)}
            >
              <Button size="small" danger>
                {t('common:button.delete')}
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <>
      {listError && <Alert type="error" showIcon message={listError} style={{ marginBottom: 12 }} />}
      <Card
        title={t('cardTitle')}
        extra={
          canOperate ? (
            <Button
              type="primary"
              onClick={() => {
                setForm(EMPTY_FORM)
                setFormError('')
                setModalOpen(true)
              }}
            >
              {t('button.bind')}
            </Button>
          ) : null
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={items}
          pagination={false}
          locale={{ emptyText: t('empty') }}
        />
      </Card>

      <Modal
        title={t('modal.title')}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        afterClose={() => {
          setForm(EMPTY_FORM)
          setFormError('')
        }}
        onOk={() => void handleBind()}
        okText={t('common:button.save')}
        cancelText={t('common:button.cancel')}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form layout="vertical" style={{ marginTop: 12 }}>
          {formError && <Alert type="error" showIcon message={formError} style={{ marginBottom: 12 }} />}
          <Form.Item label={t('form.provider')} required>
            <Select
              value={form.provider}
              options={[{ value: 'shopify', label: t('provider.shopify') }]}
              onChange={(value: string) => patchForm({ provider: value })}
            />
          </Form.Item>
          <Form.Item label={t('form.connection')} required>
            <Select
              value={form.connectionId || undefined}
              placeholder={t('form.connectionPlaceholder')}
              options={connections.map((conn) => ({
                value: conn.id,
                label: `${conn.displayName}（${conn.id}）`,
              }))}
              onChange={(value: string) => patchForm({ connectionId: value })}
            />
          </Form.Item>
          <Form.Item label={t('form.shop')} required>
            <Input
              value={form.shop}
              placeholder={t('form.shopPlaceholder')}
              onChange={(e) => patchForm({ shop: e.target.value })}
            />
          </Form.Item>
          <Form.Item label={t('form.apiVersion')}>
            <Input
              value={form.apiVersion}
              onChange={(e) => patchForm({ apiVersion: e.target.value })}
            />
          </Form.Item>
        </Form>
      </Modal>

      {webhookBinding && (
        <WebhookSubscriptionsModal
          open
          binding={webhookBinding}
          graphs={graphs}
          onClose={() => setWebhookBinding(null)}
        />
      )}
    </>
  )
}
