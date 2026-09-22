import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Layout,
  message,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  authorizeConnection,
  createConnection,
  deleteConnection,
  exchangeConnection,
  listConnections,
  refreshConnection,
  testConnection,
  updateConnection,
  type ConnectionView,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import {
  EMPTY_CONNECTION_DRAFT,
  buildConnectionInput,
  formatDateTime,
  scopesToText,
  statusColor,
  statusLabel,
  type ConnectionDraft,
} from '../lib/connections'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type ConnectionsProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

export function Connections({ principal, onLogout, onBack }: ConnectionsProps) {
  const { t } = useTranslation('connections')
  const canAdmin = roleCan(principal.role, 'administer')
  const canOperate = roleCan(principal.role, 'operate')

  const [items, setItems] = useState<ConnectionView[]>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')

  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<ConnectionView | null>(null)
  const [draft, setDraft] = useState<ConnectionDraft>(EMPTY_CONNECTION_DRAFT)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  // 完成授权弹窗
  const [authTarget, setAuthTarget] = useState<ConnectionView | null>(null)
  const [authState, setAuthState] = useState('')
  const [authCode, setAuthCode] = useState('')
  const [authBusy, setAuthBusy] = useState(false)
  const [authError, setAuthError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const data = await listConnections()
      setItems(data)
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('error.loadList'))
    } finally {
      setLoading(false)
    }
  }, [t])

  const handleManualRefresh = useCallback(() => {
    setLoading(true)
    setListError('')
    void refresh()
  }, [refresh])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  const patchDraft = (patch: Partial<ConnectionDraft>) =>
    setDraft((prev) => ({ ...prev, ...patch }))

  const openCreate = () => {
    setEditing(null)
    setDraft(EMPTY_CONNECTION_DRAFT)
    setFormError('')
    setEditorOpen(true)
  }

  const openEdit = (record: ConnectionView) => {
    setEditing(record)
    setDraft({
      provider: record.provider,
      displayName: record.displayName,
      authUrl: record.authUrl,
      tokenUrl: record.tokenUrl,
      clientId: record.clientId,
      clientSecret: '',
      scopesText: scopesToText(record.scopes),
      redirectUri: record.redirectUri,
    })
    setFormError('')
    setEditorOpen(true)
  }

  const handleSave = async () => {
    const built = buildConnectionInput(draft, { isEdit: editing !== null })
    if (built.error || !built.input) {
      setFormError(built.error ?? t('error.formInvalid'))
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await updateConnection(editing.id, built.input)
      } else {
        await createConnection(built.input)
      }
      message.success(t('message.saved'))
      setEditorOpen(false)
      await refresh()
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('error.save'))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteConnection(id)
      message.success(t('message.deleted'))
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('error.delete'))
    }
  }

  const handleAuthorize = async (record: ConnectionView) => {
    try {
      const info = await authorizeConnection(record.id)
      window.open(info.authorizeUrl, '_blank', 'noopener')
      setAuthTarget(record)
      setAuthState(info.state)
      setAuthCode('')
      setAuthError('')
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('error.authorize'))
    }
  }

  const openCompleteAuth = (record: ConnectionView) => {
    setAuthTarget(record)
    setAuthState('')
    setAuthCode('')
    setAuthError('')
  }

  const handleExchange = async () => {
    if (!authTarget) return
    const code = authCode.trim()
    const state = authState.trim()
    if (!code || !state) {
      setAuthError(t('authModal.codeStateRequired'))
      return
    }
    setAuthBusy(true)
    try {
      await exchangeConnection(authTarget.id, code, state)
      message.success(t('message.authorized'))
      setAuthTarget(null)
      await refresh()
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : t('error.authorize'))
    } finally {
      setAuthBusy(false)
    }
  }

  const handleRefreshToken = async (record: ConnectionView) => {
    try {
      await refreshConnection(record.id)
      message.success(t('message.refreshed'))
      await refresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('error.refresh'))
    }
  }

  const handleTest = async (record: ConnectionView) => {
    try {
      const result = await testConnection(record.id)
      if (result.ok) {
        message.success(t('message.testOk'))
      } else {
        message.warning(result.reason ? `${t('message.testFail')}：${result.reason}` : t('message.testFail'))
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('error.test'))
    }
  }

  const columns: ColumnsType<ConnectionView> = [
    {
      title: t('col.displayName'),
      dataIndex: 'displayName',
      key: 'displayName',
      render: (value: string, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.provider}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: t('col.status'),
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: ConnectionView['status']) => (
        <Tag color={statusColor(status)}>{t(statusLabel(status))}</Tag>
      ),
    },
    {
      title: t('col.scopes'),
      dataIndex: 'scopes',
      key: 'scopes',
      width: 180,
      ellipsis: true,
      render: (scopes: string[]) => scopes.join(' ') || '—',
    },
    {
      title: t('col.expiresAt'),
      dataIndex: 'expiresAt',
      key: 'expiresAt',
      width: 170,
      render: (value: string | null) => formatDateTime(value) || '—',
    },
    {
      title: t('col.createdAt'),
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 170,
      render: (value: string | null) => formatDateTime(value) || '—',
    },
    ...(canOperate
      ? [
          {
            title: t('col.actions'),
            key: 'actions',
            width: canAdmin ? 300 : 240,
            render: (_: unknown, record: ConnectionView) => (
              <Space size={4} wrap>
                <Button size="small" onClick={() => handleAuthorize(record)}>
                  {t('button.authorize')}
                </Button>
                <Button size="small" onClick={() => openCompleteAuth(record)}>
                  {t('button.completeAuth')}
                </Button>
                <Button size="small" onClick={() => handleRefreshToken(record)}>
                  {t('button.refresh')}
                </Button>
                <Button size="small" onClick={() => handleTest(record)}>
                  {t('button.test')}
                </Button>
                {canAdmin && (
                  <Button size="small" onClick={() => openEdit(record)}>
                    {t('button.edit')}
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
          } as ColumnsType<ConnectionView>[number],
        ]
      : []),
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={onBack}>{t('header.back')}</Button>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('title')}
          </Typography.Title>
        </Space>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <Alert type="info" showIcon message={t('notice.title')} description={t('notice.description')} />
          {listError && <Alert type="error" showIcon message={listError} />}
          <Card
            title={t('list.cardTitle')}
            extra={
              <Space>
                <Button onClick={handleManualRefresh}>{t('header.refresh')}</Button>
                {canAdmin && (
                  <Button type="primary" onClick={openCreate}>
                    {t('button.create')}
                  </Button>
                )}
              </Space>
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
        </Space>
      </Content>

      <Modal
        title={editing ? t('modal.editTitle') : t('modal.createTitle')}
        open={editorOpen}
        onCancel={() => setEditorOpen(false)}
        afterClose={() => {
          setEditing(null)
          setDraft(EMPTY_CONNECTION_DRAFT)
          setFormError('')
        }}
        onOk={() => void handleSave()}
        okText={t('common:button.save')}
        cancelText={t('common:button.cancel')}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form layout="vertical" style={{ marginTop: 12 }}>
          {formError && <Alert type="error" showIcon message={formError} style={{ marginBottom: 12 }} />}
          <Form.Item label={t('form.provider')} required>
            <Input
              value={draft.provider}
              placeholder={t('form.providerPlaceholder')}
              onChange={(e) => patchDraft({ provider: e.target.value })}
            />
          </Form.Item>
          <Form.Item label={t('form.displayName')} required>
            <Input
              value={draft.displayName}
              placeholder={t('form.displayNamePlaceholder')}
              onChange={(e) => patchDraft({ displayName: e.target.value })}
            />
          </Form.Item>
          <Form.Item label={t('form.authUrl')} required>
            <Input
              value={draft.authUrl}
              placeholder="https://idp.example.com/oauth/authorize"
              onChange={(e) => patchDraft({ authUrl: e.target.value })}
            />
          </Form.Item>
          <Form.Item label={t('form.tokenUrl')} required>
            <Input
              value={draft.tokenUrl}
              placeholder="https://idp.example.com/oauth/token"
              onChange={(e) => patchDraft({ tokenUrl: e.target.value })}
            />
          </Form.Item>
          <Form.Item label={t('form.clientId')} required>
            <Input
              value={draft.clientId}
              placeholder={t('form.clientIdPlaceholder')}
              onChange={(e) => patchDraft({ clientId: e.target.value })}
            />
          </Form.Item>
          <Form.Item
            label={t('form.clientSecret')}
            extra={editing ? t('modal.secretKeepHint') : t('form.clientSecretHint')}
          >
            <Input.Password
              value={draft.clientSecret}
              placeholder={editing ? t('form.clientSecretKeepPlaceholder') : t('form.clientSecretPlaceholder')}
              onChange={(e) => patchDraft({ clientSecret: e.target.value })}
            />
          </Form.Item>
          <Form.Item label={t('form.scopes')} extra={t('form.scopesHint')}>
            <Input
              value={draft.scopesText}
              placeholder="read write orders"
              onChange={(e) => patchDraft({ scopesText: e.target.value })}
            />
          </Form.Item>
          <Form.Item label={t('form.redirectUri')} extra={t('form.redirectUriHint')}>
            <Input
              value={draft.redirectUri}
              placeholder="http://localhost:8000/connections/callback"
              onChange={(e) => patchDraft({ redirectUri: e.target.value })}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t('authModal.title')}
        open={authTarget !== null}
        onCancel={() => setAuthTarget(null)}
        onOk={() => void handleExchange()}
        okText={t('authModal.submit')}
        cancelText={t('common:button.cancel')}
        confirmLoading={authBusy}
        destroyOnClose
      >
        <Space orientation="vertical" size="middle" style={{ width: '100%', marginTop: 12 }}>
          <Alert type="info" showIcon message={t('authModal.hint')} />
          {authError && <Alert type="error" showIcon message={authError} />}
          <Form layout="vertical" style={{ width: '100%' }}>
            <Form.Item label={t('authModal.stateLabel')}>
              <Input.TextArea
                rows={2}
                value={authState}
                onChange={(e) => setAuthState(e.target.value)}
                placeholder={t('authModal.statePlaceholder')}
              />
            </Form.Item>
            <Form.Item label={t('authModal.codeLabel')} required>
              <Input
                value={authCode}
                onChange={(e) => setAuthCode(e.target.value)}
                placeholder={t('authModal.codePlaceholder')}
              />
            </Form.Item>
          </Form>
        </Space>
      </Modal>
    </Layout>
  )
}
