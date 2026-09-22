import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
  Layout,
  Popconfirm,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  decideByEmail,
  getEmailApprovalView,
  type EmailApprovalView,
} from '../lib/apiClient'
import { formatRemaining, remainingSeconds } from '../lib/approvals'

const { Content, Header } = Layout

export function EmailApproval({ token }: { token: string }): ReactElement {
  const [view, setView] = useState<EmailApprovalView | null>(null)
  const [invalid, setInvalid] = useState(false)
  const [busy, setBusy] = useState(false)
  const [comment, setComment] = useState('')
  const [, forceTick] = useState(0)

  const refresh = useCallback(async () => {
    try {
      setView(await getEmailApprovalView(token))
      setInvalid(false)
    } catch {
      setView(null)
      setInvalid(true)
    }
  }, [token])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!view || view.status !== 'pending') return
    const timer = setInterval(() => forceTick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [view])

  const submit = useCallback(
    async (decision?: 'approved' | 'rejected', actionId?: string) => {
      setBusy(true)
      try {
        await decideByEmail(
          actionId
            ? { token, actionId, form: { comment } }
            : { token, decision, comment },
        )
        await refresh()
      } catch (exc) {
        message.error(exc instanceof Error ? exc.message : '提交失败')
      } finally {
        setBusy(false)
      }
    },
    [token, comment, refresh],
  )

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <Typography.Text strong style={{ color: '#fff', fontSize: 16 }}>
          Atlas 审批处理
        </Typography.Text>
      </Header>
      <Content style={{ display: 'flex', justifyContent: 'center', padding: 24 }}>
        <Card style={{ maxWidth: 720, width: '100%' }}>
          {invalid && (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Alert
                type="error"
                showIcon
                message="审批链接无效或已过期"
                description="链接可能已被处理、超过有效期，或地址被修改。请重新进入应用查看最新审批。"
              />
              <Button type="primary" href="/">
                前往 Atlas 应用
              </Button>
            </Space>
          )}

          {!invalid && !view && <Typography.Text type="secondary">加载中…</Typography.Text>}

          {view && (
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
              <Descriptions column={1} bordered size="small">
                <Descriptions.Item label="审批说明">{view.summary}</Descriptions.Item>
                {view.approver && (
                  <Descriptions.Item label="指定审批人">{view.approver}</Descriptions.Item>
                )}
                <Descriptions.Item label="图 / 节点">
                  {view.graphId} / {view.nodeId}
                </Descriptions.Item>
                <Descriptions.Item label="剩余时间">
                  {view.status === 'pending' ? (
                    <Tag
                      color={
                        remainingSeconds(view.createdAt, view.timeoutSeconds) > 60
                          ? 'green'
                          : 'red'
                      }
                    >
                      {formatRemaining(
                        remainingSeconds(view.createdAt, view.timeoutSeconds),
                      )}
                    </Tag>
                  ) : (
                    '—'
                  )}
                </Descriptions.Item>
              </Descriptions>

              {view.status === 'resolved' && (
                <Alert
                  type="info"
                  showIcon
                  message={`该审批已处理：${
                    view.decision === 'approved' ? '同意' : '拒绝'
                  }（来源：${view.resolvedBy === 'timeout' ? '超时自动处理' : '人工处理'}）`}
                />
              )}

              {view.status === 'pending' && (
                <>
                  <Input.TextArea
                    rows={2}
                    maxLength={500}
                    showCount
                    placeholder="审批意见（选填）"
                    value={comment}
                    onChange={(event) => setComment(event.target.value)}
                  />
                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                    {view.card ? (
                      view.card.links.map((link) => {
                        const actionId =
                          new URL(link.url, window.location.origin).searchParams.get(
                            'actionId',
                          ) ?? link.id
                        const danger = link.id === 'reject'
                        return (
                          <Popconfirm
                            key={link.id}
                            title={danger ? '确认拒绝该审批？' : '确认同意该审批？'}
                            onConfirm={() => void submit(undefined, actionId)}
                          >
                            <Button danger={danger} type={danger ? 'default' : 'primary'} loading={busy}>
                              {link.label}
                            </Button>
                          </Popconfirm>
                        )
                      })
                    ) : (
                      <>
                        <Popconfirm
                          title="确认拒绝该审批？"
                          onConfirm={() => void submit('rejected')}
                        >
                          <Button danger loading={busy}>
                            拒绝
                          </Button>
                        </Popconfirm>
                        <Popconfirm
                          title="确认同意该审批？"
                          onConfirm={() => void submit('approved')}
                        >
                          <Button type="primary" loading={busy}>
                            同意
                          </Button>
                        </Popconfirm>
                      </>
                    )}
                  </div>
                </>
              )}
            </Space>
          )}
        </Card>
      </Content>
    </Layout>
  )
}
