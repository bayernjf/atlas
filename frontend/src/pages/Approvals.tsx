import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  Button,
  Card,
  Layout,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
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

const { Content, Header } = Layout

type ApprovalsProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

export function Approvals({ principal, onLogout, onBack }: ApprovalsProps): ReactElement {
  const [items, setItems] = useState<QueueApprovalItem[]>([])
  const [loading, setLoading] = useState(false)
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
      message.error(exc instanceof Error ? exc.message : '加载审批队列失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void refresh()
        forceTick((n) => n + 1)
      }
    }, 10_000)
    return () => clearInterval(timer)
  }, [refresh])

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
      message.error(exc instanceof Error ? exc.message : '提交失败')
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
      message.error(exc instanceof Error ? exc.message : '提交失败')
    }
  }

  const columns: ColumnsType<QueueApprovalItem> = [
    { title: '审批说明', dataIndex: 'summary' },
    { title: '指定审批人', dataIndex: 'approver', width: 110 },
    { title: '图', dataIndex: 'graph_id', width: 90 },
    { title: '节点', dataIndex: 'node_id', width: 100 },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (value: number) => formatCreatedAt(value),
    },
    {
      title: '剩余时间',
      width: 110,
      render: (_, row) => {
        const remaining = remainingSeconds(row.createdAt, row.timeoutSeconds)
        return (
          <Tag color={remaining > 60 ? 'green' : 'red'}>{formatRemaining(remaining)}</Tag>
        )
      },
    },
    {
      title: '操作',
      width: 200,
      render: (_, row) =>
        canDecide ? (
          <Space size="small">
            {row.cardTemplateId && (
              <Button size="small" onClick={() => setCardToken(row.token)}>
                查看卡片
              </Button>
            )}
            <Popconfirm
              title="确认同意？"
              onConfirm={() => void handleDecide(row.token, 'approved')}
            >
              <Button size="small" type="primary">
                同意
              </Button>
            </Popconfirm>
            <Popconfirm
              title="确认拒绝？"
              onConfirm={() => void handleDecide(row.token, 'rejected')}
            >
              <Button size="small" danger>
                拒绝
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <Typography.Text type="secondary">只读</Typography.Text>
        ),
    },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Typography.Text strong style={{ color: '#fff', fontSize: 16 }}>
          审批队列
        </Typography.Text>
        <div style={{ flex: 1 }} />
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content style={{ padding: 24 }}>
        <Card
          title="待处理审批"
          extra={
            <Space>
              <Typography.Text type="secondary">每 10 秒自动刷新</Typography.Text>
              <Button onClick={() => void refresh()} loading={loading}>
                手动刷新
              </Button>
              <Button onClick={onBack}>返回</Button>
            </Space>
          }
        >
          <Table
            rowKey="token"
            columns={columns}
            dataSource={items}
            loading={loading}
            pagination={false}
            locale={{ emptyText: '当前没有待处理的审批请求' }}
          />
        </Card>
      </Content>

      <Modal
        open={cardToken !== null}
        title="审批卡片"
        footer={null}
        width={640}
        onCancel={() => setCardToken(null)}
      >
        {cardError && <Typography.Text type="danger">{cardError}</Typography.Text>}
        {cardView ? (
          <CardRenderer card={cardView} onDecide={handleCardAction} />
        ) : (
          !cardError && <Typography.Text type="secondary">卡片加载中…</Typography.Text>
        )}
      </Modal>
    </Layout>
  )
}
