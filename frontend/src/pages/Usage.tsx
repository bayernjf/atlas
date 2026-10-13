import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Layout,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import {
  getUsageRecent,
  getUsageSummary,
  type UsageRecordView,
  type UsageSummaryItem,
} from '../lib/apiClient'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type UsageProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

type GroupBy = 'caller' | 'model' | 'day'

const GROUP_OPTIONS: GroupBy[] = ['caller', 'model', 'day']

function StatusTag({ status, t }: { status: string; t: (key: string) => string }) {
  return (
    <Tag color={status === 'ok' ? 'green' : 'red'}>
      {status === 'ok' ? t('status.ok') : t('status.error')}
    </Tag>
  )
}

export function Usage({ principal, onLogout, onBack }: UsageProps) {
  const { t } = useTranslation('usage')
  const [groupBy, setGroupBy] = useState<GroupBy>('caller')
  const [summary, setSummary] = useState<UsageSummaryItem[]>([])
  const [recent, setRecent] = useState<UsageRecordView[]>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [summaryBody, recentBody] = await Promise.all([
        getUsageSummary({ groupBy }),
        getUsageRecent(50),
      ])
      setSummary(summaryBody.items)
      setRecent(recentBody)
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('error.load'))
    } finally {
      setLoading(false)
    }
  }, [groupBy, t])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  const summaryColumns = [
    { title: t('summary.key'), dataIndex: 'key', key: 'key' },
    { title: t('summary.calls'), dataIndex: 'calls', key: 'calls' },
    { title: t('summary.promptTokens'), dataIndex: 'promptTokens', key: 'promptTokens' },
    {
      title: t('summary.completionTokens'),
      dataIndex: 'completionTokens',
      key: 'completionTokens',
    },
    { title: t('summary.totalTokens'), dataIndex: 'totalTokens', key: 'totalTokens' },
    { title: t('summary.errors'), dataIndex: 'errors', key: 'errors' },
  ]

  const recentColumns = [
    { title: t('recent.ts'), dataIndex: 'ts', key: 'ts', width: 200 },
    {
      title: t('recent.caller'),
      dataIndex: 'caller',
      key: 'caller',
      render: (caller: string) => t(`caller.${caller}`, { defaultValue: caller }),
    },
    { title: t('recent.model'), dataIndex: 'model', key: 'model' },
    { title: t('recent.totalTokens'), dataIndex: 'totalTokens', key: 'totalTokens' },
    { title: t('recent.latency'), dataIndex: 'latencyMs', key: 'latencyMs' },
    {
      title: t('recent.status'),
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => <StatusTag status={status} t={t} />,
    },
    { title: t('recent.nodeId'), dataIndex: 'nodeId', key: 'nodeId' },
    { title: t('recent.runId'), dataIndex: 'runId', key: 'runId' },
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Typography.Title level={3}>{t('title')}</Typography.Title>
        <Space>
          <Button onClick={onBack}>{t('header.back')}</Button>
          <Button onClick={() => void refresh()} loading={loading}>
            {t('header.refresh')}
          </Button>
          <UserBadge principal={principal} onLogout={onLogout} />
        </Space>
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Card>
            <Typography.Title level={4}>{t('notice.title')}</Typography.Title>
            <Typography.Paragraph>{t('notice.description')}</Typography.Paragraph>
          </Card>

          {listError && <Alert type="error" message={listError} showIcon />}

          <Card
            loading={loading}
            title={t('summary.title')}
            extra={
              <Segmented
                value={groupBy}
                onChange={(value) => setGroupBy(value as GroupBy)}
                options={GROUP_OPTIONS.map((option) => ({
                  value: option,
                  label:
                    option === 'caller'
                      ? t('summary.groupCaller')
                      : option === 'model'
                        ? t('summary.groupModel')
                        : t('summary.groupDay'),
                }))}
              />
            }
          >
            <Table
              rowKey="key"
              size="small"
              dataSource={summary}
              columns={summaryColumns}
              locale={{ emptyText: t('summary.empty') }}
              pagination={false}
            />
          </Card>

          <Card title={t('recent.title')} loading={loading}>
            <Table
              rowKey={(row) => `${row.ts}-${row.caller}-${row.model}-${row.runId}-${row.nodeId}`}
              size="small"
              dataSource={recent}
              columns={recentColumns}
              locale={{ emptyText: t('recent.empty') }}
              pagination={false}
              scroll={{ x: 'max-content' }}
            />
            <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
              {t('recent.hint')}
            </Typography.Paragraph>
          </Card>
        </Space>
      </Content>
    </Layout>
  )
}
