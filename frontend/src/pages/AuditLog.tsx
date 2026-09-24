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
import { cleanText, mergeAuditPages, utcBounds } from '../lib/auditFilters'
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
  const [loadingMore, setLoadingMore] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [limit, setLimit] = useState(100)
  const [actionInput, setActionInput] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [actorInput, setActorInput] = useState('')
  const [actorFilter, setActorFilter] = useState('')
  // 原生 datetime-local 的编辑值（本地无时区串）与已生效值分开存，按「筛选」才提交。
  const [sinceInput, setSinceInput] = useState('')
  const [untilInput, setUntilInput] = useState('')
  const [sinceFilter, setSinceFilter] = useState('')
  const [untilFilter, setUntilFilter] = useState('')
  const [nextCursor, setNextCursor] = useState<number | null>(null)

  // docs/61 §4.3：过滤条件变了就整页替换（游标从头开始），加载更多才追加。
  const load = useCallback(
    async (cursor: number | null, append: boolean) => {
      if (append) setLoadingMore(true)
      else setLoading(true)
      const bounds = utcBounds(sinceFilter, untilFilter)
      try {
        const body = await listAuditEvents(
          limit,
          actionFilter,
          { actor: actorFilter || undefined, ...bounds },
          cursor,
        )
        setItems((current) => (append ? mergeAuditPages(current, body.items) : body.items))
        setNextCursor(body.nextCursor)
      } catch (exc) {
        message.error(exc instanceof Error ? exc.message : t('loadError'))
      } finally {
        setLoading(false)
        setLoadingMore(false)
      }
    },
    [limit, actionFilter, actorFilter, sinceFilter, untilFilter, t],
  )

  const refresh = useCallback(() => load(null, false), [load])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function handleExport(): Promise<void> {
    setExporting(true)
    try {
      const blob = await exportAuditJsonl(actionFilter, {
        actor: actorFilter || undefined,
        ...utcBounds(sinceFilter, untilFilter),
      })
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

  function applyFilters(): void {
    setActionFilter(cleanText(actionInput) ?? '')
    setActorFilter(cleanText(actorInput) ?? '')
    setSinceFilter(sinceInput)
    setUntilFilter(untilInput)
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
            <Space wrap>
              <Input
                allowClear
                placeholder={t('filter.actionPlaceholder')}
                value={actionInput}
                style={{ width: 220 }}
                onChange={(event) => setActionInput(event.target.value)}
                onPressEnter={applyFilters}
              />
              <Input
                allowClear
                placeholder={t('filter.actorPlaceholder')}
                value={actorInput}
                style={{ width: 150 }}
                onChange={(event) => setActorInput(event.target.value)}
                onPressEnter={applyFilters}
              />
              {/* 原生 datetime-local：AntD RangePicker 会把 dayjs 拖成直接依赖且中文面板需另配 locale（docs/61 收口注记） */}
              <label className="audit-time-label">
                <span>{t('filter.since')}</span>
                <input
                  className="audit-time-input"
                  type="datetime-local"
                  step="1"
                  value={sinceInput}
                  onChange={(event) => setSinceInput(event.target.value)}
                />
              </label>
              <label className="audit-time-label">
                <span>{t('filter.until')}</span>
                <input
                  className="audit-time-input"
                  type="datetime-local"
                  step="1"
                  value={untilInput}
                  onChange={(event) => setUntilInput(event.target.value)}
                />
              </label>
              <Button type="primary" onClick={applyFilters}>
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
          {/* docs/61 §4.3：游标翻页（nextCursor 为 null 即到底），不做客户端假分页。 */}
          {nextCursor !== null && (
            <div style={{ marginTop: 12, textAlign: 'center' }}>
              <Button loading={loadingMore} onClick={() => void load(nextCursor, true)}>
                {t('button.loadMore')}
              </Button>
            </div>
          )}
        </Card>
      </Content>
    </Layout>
  )
}
