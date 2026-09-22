import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Empty, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  deleteWebhookDeadLetter,
  getWebhookMetrics,
  listWebhookDeadLetters,
  replayWebhookDeadLetter,
  type WebhookDeadLetter,
  type WebhookDeliveryMetrics,
} from '../../lib/apiClient'
import { formatTime } from '../../lib/monitoring'
import { useTranslation } from '../../locales'

const { Text } = Typography

const EMPTY_METRICS: WebhookDeliveryMetrics = {
  byTopic: {},
  totals: { received: 0, dead: 0, duplicates: 0},
}

type Props = {
  canOperate: boolean
  canAdmin: boolean
}

/**
 * 入站 webhook 可靠性卡（docs/40 §1C/§1E）：实时投递指标 + 按 topic 分布 + 死信列表，
 * operate 可一键重放、admin 可删除；viewer 只读。
 */
export function WebhookReliabilityCard({ canOperate, canAdmin }: Props) {
  const { t } = useTranslation('monitoring')
  const [metrics, setMetrics] = useState<WebhookDeliveryMetrics>(EMPTY_METRICS)
  const [deadLetters, setDeadLetters] = useState<WebhookDeadLetter[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [metricsData, letters] = await Promise.all([
        getWebhookMetrics(),
        listWebhookDeadLetters({ limit: 100 }),
      ])
      setMetrics(metricsData)
      setDeadLetters(letters)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getWebhookMetrics()
      .then((data) => {
        if (!cancelled) setMetrics(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    listWebhookDeadLetters({ limit: 100 })
      .then((items) => {
        if (!cancelled) setDeadLetters(items)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function replay(webhookId: string) {
    setBusyId(webhookId)
    try {
      const result = await replayWebhookDeadLetter(webhookId)
      if (result.status === 'received') {
        setDeadLetters((prev) => prev.filter((item) => item.webhookId !== webhookId))
      } else {
        await refresh()
      }
    } finally {
      setBusyId(null)
    }
  }

  async function remove(webhookId: string) {
    setBusyId(webhookId)
    try {
      await deleteWebhookDeadLetter(webhookId)
      setDeadLetters((prev) => prev.filter((item) => item.webhookId !== webhookId))
    } finally {
      setBusyId(null)
    }
  }

  const topicRows = Object.entries(metrics.byTopic).map(([topic, values]) => ({
    topic, ...values,
  }))

  const reasonLabel = (code: string) => t(`webhookReliability.reason.${code}`)

  const deadColumns: ColumnsType<WebhookDeadLetter> = [
    {
      title: t('webhookReliability.col.topic'),
      dataIndex: 'topic',
      width: 130,
      render: (topic: string) => <Text className="font-mono">{topic}</Text>,
    },
    { title: t('webhookReliability.col.shop'), dataIndex: 'shop', width: 180 },
    {
      title: t('webhookReliability.col.reasons'),
      dataIndex: 'reasons',
      render: (reasons: WebhookDeadLetter['reasons']) => (
        <Space wrap size={4}>
          {reasons.map((reason, idx) => (
            <Tag key={`${reason.graphId}-${idx}`} color="volcano">
              {reasonLabel(reason.code)}
              {reason.graphId ? ` · ${reason.graphId}` : ''}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: t('webhookReliability.col.createdAt'),
      dataIndex: 'createdAt',
      width: 150,
      render: (value: string) => formatTime(value),
    },
    ...(canOperate || canAdmin
      ? [
          {
            title: t('webhookReliability.col.actions'),
            key: 'actions',
            width: 170,
            render: (_: unknown, record: WebhookDeadLetter) => (
              <Space>
                {canOperate && (
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    loading={busyId === record.webhookId}
                    onClick={() => replay(record.webhookId)}
                  >
                    {t('webhookReliability.action.replay')}
                  </Button>
                )}
                {canAdmin && (
                  <Popconfirm
                    title={t('webhookReliability.deleteConfirm')}
                    onConfirm={() => remove(record.webhookId)}
                  >
                    <Button size="small" danger loading={busyId === record.webhookId}>
                      {t('common:button.delete')}
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          } satisfies ColumnsType<WebhookDeadLetter>[number],
        ]
      : []),
  ]

  return (
    <Card
      title={
        <Space>
          {t('webhookReliability.card.title')}
          {metrics.totals.dead > 0 && <Tag color="red">{metrics.totals.dead}</Tag>}
        </Space>
      }
      extra={
        <Button size="small" onClick={refresh} loading={loading}>
          {t('header.refresh')}
        </Button>
      }
    >
      {error && <Text type="danger">{error}</Text>}
      <Space wrap size="large" style={{ marginBottom: 16 }}>
        <Text strong>
          {t('webhookReliability.stat.received')}：{metrics.totals.received}
        </Text>
        <Text strong type={metrics.totals.dead > 0 ? 'danger' : undefined}>
          {t('webhookReliability.stat.dead')}：{metrics.totals.dead}
        </Text>
        <Text strong type="secondary">
          {t('webhookReliability.stat.duplicates')}：{metrics.totals.duplicates}
        </Text>
      </Space>
      <Table
        size="small"
        rowKey="topic"
        pagination={false}
        dataSource={topicRows}
        locale={{ emptyText: t('webhookReliability.empty.topic') }}
        columns={[
          { title: t('webhookReliability.col.topic'), dataIndex: 'topic' },
          { title: t('webhookReliability.stat.received'), dataIndex: 'received', width: 100 },
          {
            title: t('webhookReliability.stat.dead'),
            dataIndex: 'dead',
            width: 100,
            render: (value: number) => (value > 0 ? <Tag color="red">{value}</Tag> : 0),
          },
          { title: t('webhookReliability.stat.duplicates'), dataIndex: 'duplicates', width: 100 },
        ]}
        style={{ marginBottom: 20 }}
      />
      {deadLetters.length === 0 && !loading ? (
        <Empty description={t('webhookReliability.empty.dead')} />
      ) : (
        <Table<WebhookDeadLetter>
          size="small"
          rowKey="webhookId"
          loading={loading}
          pagination={false}
          dataSource={deadLetters}
          columns={deadColumns}
          scroll={{ x: 760 }}
        />
      )}
    </Card>
  )
}
