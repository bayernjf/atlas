import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  Button,
  Card,
  Input,
  Layout,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UserBadge } from '../components/UserBadge'
import {
  exportAuditJsonl,
  listAuditEvents,
  type AuditEventItem,
} from '../lib/apiClient'
import type { Principal } from '../lib/auth'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type AuditLogProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

export function AuditLog({ principal, onLogout, onBack }: AuditLogProps): ReactElement {
  const { t } = useTranslation('audit')
  const [items, setItems] = useState<AuditEventItem[]>([])
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [limit, setLimit] = useState(100)
  const [actionInput, setActionInput] = useState('')
  const [actionFilter, setActionFilter] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const body = await listAuditEvents(limit, actionFilter)
      setItems(body.items)
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('loadError'))
    } finally {
      setLoading(false)
    }
  }, [limit, actionFilter, t])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function handleExport(): Promise<void> {
    setExporting(true)
    try {
      const blob = await exportAuditJsonl(actionFilter)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'atlas-audit.jsonl'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      message.success(t('exported'))
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('exportError'))
    } finally {
      setExporting(false)
    }
  }

  const columns: ColumnsType<AuditEventItem> = [
    { title: t('column.at'), dataIndex: 'at', width: 260 },
    { title: t('column.actor'), dataIndex: 'actor', width: 120 },
    { title: t('column.action'), dataIndex: 'action', width: 300 },
    {
      title: t('column.statusCode'),
      dataIndex: 'statusCode',
      width: 100,
      render: (value: number) => (
        <Tag color={value < 400 ? 'green' : value < 500 ? 'orange' : 'red'}>{value}</Tag>
      ),
    },
    { title: t('column.path'), dataIndex: 'path' },
    { title: t('column.ip'), dataIndex: 'ip', width: 130 },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Typography.Text strong style={{ color: '#fff', fontSize: 16 }}>
          {t('title')}
        </Typography.Text>
        <div style={{ flex: 1 }} />
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content style={{ padding: 24 }}>
        <Card
          title={t('card.title')}
          extra={
            <Space>
              <Input
                allowClear
                placeholder={t('filter.actionPlaceholder')}
                value={actionInput}
                style={{ width: 220 }}
                onChange={(event) => setActionInput(event.target.value)}
                onPressEnter={() => setActionFilter(actionInput.trim())}
              />
              <Button
                type="primary"
                onClick={() => setActionFilter(actionInput.trim())}
              >
                {t('filter.search')}
              </Button>
              <Select
                value={limit}
                style={{ width: 100 }}
                onChange={setLimit}
                options={[50, 100, 200].map((value) => ({
                  value,
                  label: `${t('filter.limit')} ${value}`,
                }))}
              />
              <Button onClick={() => void refresh()} loading={loading}>
                {t('button.refresh')}
              </Button>
              <Button onClick={() => void handleExport()} loading={exporting}>
                {t('button.export')}
              </Button>
              <Button onClick={onBack}>{t('button.back')}</Button>
            </Space>
          }
        >
          <Table
            rowKey="id"
            columns={columns}
            dataSource={items}
            loading={loading}
            pagination={false}
            scroll={{ x: 1100 }}
            locale={{ emptyText: t('empty') }}
          />
        </Card>
      </Content>
    </Layout>
  )
}
