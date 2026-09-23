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
import { useTranslation } from '../locales'

const { Content, Header } = Layout

export function EmailApproval({ token }: { token: string }): ReactElement {
  const { t } = useTranslation('approvals')
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
        message.error(exc instanceof Error ? exc.message : t('error.submitFailed'))
      } finally {
        setBusy(false)
      }
    },
    [token, comment, refresh, t],
  )

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <Typography.Text strong style={{ color: '#fff', fontSize: 16 }}>
          {t('email.header')}
        </Typography.Text>
      </Header>
      <Content style={{ display: 'flex', justifyContent: 'center', padding: 24 }}>
        <Card style={{ maxWidth: 720, width: '100%' }}>
          {invalid && (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Alert
                type="error"
                showIcon
                message={t('email.invalid')}
                description={t('email.invalidHint')}
              />
              <Button type="primary" href="/">
                {t('email.goToApp')}
              </Button>
            </Space>
          )}

          {!invalid && !view && <Typography.Text type="secondary">{t('email.loading')}</Typography.Text>}

          {view && (
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
              <Descriptions column={1} bordered size="small">
                <Descriptions.Item label={t('email.labelSummary')}>{view.summary}</Descriptions.Item>
                {view.approver && (
                  <Descriptions.Item label={t('email.labelApprover')}>{view.approver}</Descriptions.Item>
                )}
                <Descriptions.Item label={t('email.labelGraphNode')}>
                  {view.graphId} / {view.nodeId}
                </Descriptions.Item>
                <Descriptions.Item label={t('email.labelRemaining')}>
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
                  message={t('email.resolvedLine', {
                    decision:
                      view.decision === 'approved' ? t('actions.approve') : t('actions.reject'),
                    source: t(
                      view.resolvedBy === 'timeout' ? 'source.timeout' : 'source.human',
                    ),
                  })}
                />
              )}

              {view.status === 'pending' && (
                <>
                  <Input.TextArea
                    rows={2}
                    maxLength={500}
                    showCount
                    placeholder={t('email.commentPlaceholder')}
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
                            title={danger ? t('email.confirmReject') : t('email.confirmApprove')}
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
                          title={t('email.confirmReject')}
                          onConfirm={() => void submit('rejected')}
                        >
                          <Button danger loading={busy}>
                            {t('actions.reject')}
                          </Button>
                        </Popconfirm>
                        <Popconfirm
                          title={t('email.confirmApprove')}
                          onConfirm={() => void submit('approved')}
                        >
                          <Button type="primary" loading={busy}>
                            {t('actions.approve')}
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
