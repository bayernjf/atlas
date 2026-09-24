import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  Button,
  Card,
  Layout,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UserBadge } from '../components/UserBadge'
import { CardRenderer } from '../components/approval/CardRenderer'
import {
  decideApproval,
  decideCardAction,
  getApprovalCard,
  listApprovalsQueue,
  listDecidedApprovals,
  type DecidedApprovalItem,
  type QueueApprovalItem,
  type WebCardView,
} from '../lib/apiClient'
import type { Principal } from '../lib/auth'
import {
  formatCreatedAt,
  formatRemaining,
  remainingSeconds,
} from '../lib/approvals'
import { roleCan } from '../lib/auth'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

/** 已处理来源枚举 → approvals namespace i18n 键（docs/57 D-4 冒烟补抽） */
const RESOLVED_SOURCE_KEYS: Record<string, string> = {
  human: 'source.human',
  'email-link': 'source.emailLink',
  timeout: 'source.timeout',
  input: 'source.input',
}

type ApprovalsProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

export function Approvals({ principal, onLogout, onBack }: ApprovalsProps): ReactElement {
  const { t } = useTranslation('approvals')
  const [activeTab, setActiveTab] = useState<'pending' | 'decided'>('pending')
  const [items, setItems] = useState<QueueApprovalItem[]>([])
  const [loading, setLoading] = useState(false)
  const [decidedItems, setDecidedItems] = useState<DecidedApprovalItem[]>([])
  const [decidedLoading, setDecidedLoading] = useState(false)
  const [decidedLoaded, setDecidedLoaded] = useState(false)
  const [cardToken, setCardToken] = useState<string | null>(null)
  const [cardView, setCardView] = useState<WebCardView | null>(null)
  const [cardError, setCardError] = useState('')
  const [, forceTick] = useState(0)
  const canDecide = roleCan(principal.role, 'operate')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await listApprovalsQueue())
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('error.loadQueue'))
    } finally {
      setLoading(false)
    }
  }, [t])

  const refreshDecided = useCallback(async () => {
    setDecidedLoading(true)
    try {
      const body = await listDecidedApprovals(50)
      setDecidedItems(body.items)
      setDecidedLoaded(true)
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('error.loadDecided'))
    } finally {
      setDecidedLoading(false)
    }
  }, [t])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 已处理 Tab：切到时加载一次，之后仅手动刷新；不轮询。
  useEffect(() => {
    if (activeTab === 'decided' && !decidedLoaded) void refreshDecided()
  }, [activeTab, decidedLoaded, refreshDecided])

  useEffect(() => {
    const timer = setInterval(() => {
      if (activeTab === 'pending' && document.visibilityState === 'visible') {
        void refresh()
        forceTick((n) => n + 1)
      }
    }, 10_000)
    return () => clearInterval(timer)
  }, [refresh, activeTab])

  useEffect(() => {
    if (cardToken === null) return
    setCardView(null)
    setCardError('')
    getApprovalCard(cardToken, 'web')
      .then((rendered) => {
        if (rendered.channel === 'web') setCardView(rendered)
      })
      .catch((exc: Error) => setCardError(exc.message))
  }, [cardToken])

  async function handleDecide(
    token: string,
    decision: 'approved' | 'rejected',
  ): Promise<void> {
    try {
      await decideApproval(token, decision)
      await refresh()
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('error.submitFailed'))
    }
  }

  async function handleCardAction(
    actionId: string,
    form: Record<string, string>,
  ): Promise<void> {
    if (!cardToken) return
    try {
      await decideCardAction(cardToken, actionId, form)
      setCardToken(null)
      await refresh()
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('error.submitFailed'))
    }
  }

  const columns: ColumnsType<QueueApprovalItem> = [
    { title: t('columns.summary'), dataIndex: 'summary' },
    { title: t('columns.approver'), dataIndex: 'approver', width: 110 },
    { title: t('columns.graph'), dataIndex: 'graph_id', width: 90 },
    { title: t('columns.node'), dataIndex: 'node_id', width: 100 },
    {
      title: t('columns.createdAt'),
      dataIndex: 'createdAt',
      width: 170,
      render: (value: number) => formatCreatedAt(value),
    },
    {
      title: t('columns.remaining'),
      width: 110,
      render: (_, row) => {
        const remaining = remainingSeconds(row.createdAt, row.timeoutSeconds)
        return (
          <Tag color={remaining > 60 ? 'green' : 'red'}>{formatRemaining(remaining)}</Tag>
        )
      },
    },
    {
      title: t('columns.actions'),
      width: 200,
      render: (_, row) =>
        canDecide ? (
          <Space size="small">
            {row.cardTemplateId && (
              <Button size="small" onClick={() => setCardToken(row.token)}>
                {t('actions.viewCard')}
              </Button>
            )}
            <Popconfirm
              title={t('actions.confirmApprove')}
              onConfirm={() => void handleDecide(row.token, 'approved')}
            >
              <Button size="small" type="primary">
                {t('actions.approve')}
              </Button>
            </Popconfirm>
            <Popconfirm
              title={t('actions.confirmReject')}
              onConfirm={() => void handleDecide(row.token, 'rejected')}
            >
              <Button size="small" danger>
                {t('actions.reject')}
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <Typography.Text type="secondary">{t('actions.readonly')}</Typography.Text>
        ),
    },
  ]

  const decidedColumns: ColumnsType<DecidedApprovalItem> = [
    { title: t('columns.summary'), dataIndex: 'summary' },
    { title: t('columns.graph'), dataIndex: 'graph_id', width: 90 },
    { title: t('columns.node'), dataIndex: 'node_id', width: 100 },
    {
      title: t('columns.decision'),
      dataIndex: 'decision',
      width: 100,
      render: (value: string) => (
        <Tag color={value === 'approved' ? 'green' : 'red'}>
          {value === 'approved' ? t('actions.approve') : t('actions.reject')}
        </Tag>
      ),
    },
    {
      title: t('columns.source'),
      dataIndex: 'resolvedBy',
      width: 130,
      render: (value: string) => {
        const key = RESOLVED_SOURCE_KEYS[value]
        return key ? t(key) : value
      },
    },
    {
      title: t('columns.comment'),
      dataIndex: 'comment',
      width: 140,
      render: (value: string) =>
        value ? (
          <Tooltip title={value}>
            <Typography.Text style={{ maxWidth: 120 }} ellipsis>
              {value}
            </Typography.Text>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">-</Typography.Text>
        ),
    },
    {
      title: t('columns.createdAt'),
      dataIndex: 'createdAt',
      width: 170,
      render: (value: number) => formatCreatedAt(value),
    },
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
          extra={
            <Space>
              <Button onClick={onBack}>{t('back')}</Button>
            </Space>
          }
        >
          <Tabs
            activeKey={activeTab}
            onChange={(key) => setActiveTab(key as 'pending' | 'decided')}
            items={[
              {
                key: 'pending',
                label: t('tabs.pending'),
                children: (
                  <>
                    <Space style={{ marginBottom: 12 }}>
                      <Typography.Text type="secondary">
                        {t('autoRefresh')}
                      </Typography.Text>
                      <Button onClick={() => void refresh()} loading={loading}>
                        {t('manualRefresh')}
                      </Button>
                    </Space>
                    <Table
                      rowKey="token"
                      columns={columns}
                      dataSource={items}
                      loading={loading}
                      pagination={false}
                      locale={{ emptyText: t('empty') }}
                    />
                  </>
                ),
              },
              {
                key: 'decided',
                label: t('tabs.decided'),
                children: (
                  <>
                    <Space style={{ marginBottom: 12 }}>
                      <Button
                        onClick={() => void refreshDecided()}
                        loading={decidedLoading}
                      >
                        {t('manualRefresh')}
                      </Button>
                    </Space>
                    <Table
                      rowKey="token"
                      columns={decidedColumns}
                      dataSource={decidedItems}
                      loading={decidedLoading}
                      pagination={false}
                      locale={{ emptyText: t('emptyDecided') }}
                    />
                  </>
                ),
              },
            ]}
          />
        </Card>
      </Content>

      <Modal
        open={cardToken !== null}
        title={t('cardModalTitle')}
        footer={null}
        width={640}
        onCancel={() => setCardToken(null)}
      >
        {cardError && <Typography.Text type="danger">{cardError}</Typography.Text>}
        {cardView ? (
          <CardRenderer card={cardView} onDecide={handleCardAction} />
        ) : (
          !cardError && <Typography.Text type="secondary">{t('cardLoading')}</Typography.Text>
        )}
      </Modal>
    </Layout>
  )
}
