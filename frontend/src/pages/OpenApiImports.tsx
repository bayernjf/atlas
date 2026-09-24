import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Input,
  Layout,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message as antdMessage,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UserBadge } from '../components/UserBadge'
import {
  deleteOpenApiImport,
  purgeOpenApiImport,
  importOpenApi,
  listOpenApiImports,
  previewOpenApi,
  putOpenApiCredentials,
  type CredentialValue,
  type ImportedSpec,
  type OpenApiPreview,
  type OperationDescriptor,
  type SecurityScheme,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { formatDateTime } from '../lib/connections'
import {
  compactCredentialDrafts,
  credentialUpsertPayload,
  emptyBasic,
  isBasicCredential,
} from '../lib/openApiCredentials'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type OpenApiImportsProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

const METHOD_COLORS: Record<string, string> = {
  get: 'green',
  post: 'blue',
  put: 'orange',
  patch: 'purple',
  delete: 'red',
}

const PERMISSION_COLORS: Record<string, string> = {
  read: 'green',
  write: 'blue',
  delete: 'red',
  financial: 'gold',
}

function methodTag(method: string) {
  const key = method.toLowerCase()
  return <Tag color={METHOD_COLORS[key] ?? 'default'}>{key.toUpperCase()}</Tag>
}

function permissionTag(permission: string, t: (key: string) => string) {
  return (
    <Tag color={PERMISSION_COLORS[permission] ?? 'default'}>
      {t(`permission.${permission}`)}
    </Tag>
  )
}

function schemeLabel(
  scheme: SecurityScheme,
  t: (key: string, params?: Record<string, unknown>) => string,
) {
  if (scheme.kind === 'bearer') {
    return t('credentials.label.bearer', { name: scheme.name })
  }
  if (scheme.kind === 'basic') {
    return t('credentials.label.basic', { name: scheme.name })
  }
  const kind = scheme.location === 'query' ? 'query' : 'header'
  return t(`credentials.label.${kind}`, { name: scheme.param })
}

function CredentialFields({
  schemes,
  values,
  t,
  onChange,
}: {
  schemes: SecurityScheme[]
  values: Record<string, CredentialValue>
  t: (key: string, params?: Record<string, unknown>) => string
  onChange: (name: string, value: CredentialValue) => void
}) {
  if (schemes.length === 0) return null
  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%', marginTop: 16 }}>
      <div>
        <Typography.Text strong>{t('credentials.sectionTitle')}</Typography.Text>
        <div>
          <Typography.Text type="secondary">{t('credentials.sectionHint')}</Typography.Text>
        </div>
      </div>
      {schemes.map((scheme) => {
        const draft = values[scheme.name]
        if (scheme.kind === 'basic') {
          const fields = isBasicCredential(draft) ? draft : emptyBasic()
          const patch = (next: Partial<typeof fields>) =>
            onChange(scheme.name, { ...fields, ...next })
          return (
            <div key={scheme.name}>
              <Typography.Text>{schemeLabel(scheme, t)}</Typography.Text>
              <Space orientation="vertical" size="small" style={{ width: '100%' }}>
                <Input
                  value={fields.username}
                  placeholder={t('credentials.username')}
                  autoComplete="username"
                  onChange={(event) => patch({ username: event.target.value })}
                />
                <Input.Password
                  value={fields.password}
                  placeholder={t('credentials.password')}
                  autoComplete="current-password"
                  onChange={(event) => patch({ password: event.target.value })}
                />
              </Space>
            </div>
          )
        }
        return (
          <div key={scheme.name}>
            <Typography.Text>{schemeLabel(scheme, t)}</Typography.Text>
            <Input.Password
              value={typeof draft === 'string' ? draft : ''}
              placeholder={t('credentials.placeholder')}
              autoComplete="new-password"
              onChange={(event) => onChange(scheme.name, event.target.value)}
            />
          </div>
        )
      })}
    </Space>
  )
}

export function OpenApiImports({ principal, onLogout, onBack }: OpenApiImportsProps) {
  const { t } = useTranslation('openapi')
  const canOperate = roleCan(principal.role, 'operate')
  const canAdmin = roleCan(principal.role, 'administer')

  const [sourceTab, setSourceTab] = useState<'paste' | 'url'>('paste')
  const [content, setContent] = useState('')
  const [url, setUrl] = useState('')
  const [preview, setPreview] = useState<OpenApiPreview | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [credentialDraft, setCredentialDraft] = useState<Record<string, CredentialValue>>({})

  const [credentialSpec, setCredentialSpec] = useState<ImportedSpec | null>(null)
  const [credentialValues, setCredentialValues] = useState<Record<string, CredentialValue>>({})
  const [savingCredentials, setSavingCredentials] = useState(false)

  const [items, setItems] = useState<ImportedSpec[]>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')
  const [showDeleted, setShowDeleted] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setItems(await listOpenApiImports(showDeleted))
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('imports.title'))
    } finally {
      setLoading(false)
    }
  }, [t, showDeleted])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  const handlePreview = async () => {
    if (sourceTab === 'paste' && !content.trim()) {
      setPreviewError(t('source.pasteRequired'))
      return
    }
    if (sourceTab === 'url' && !url.trim()) {
      setPreviewError(t('source.urlRequired'))
      return
    }
    setPreviewError('')
    setPreviewing(true)
    try {
      const source = sourceTab === 'paste' ? { content } : { url: url.trim() }
      setCredentialDraft({})
      setPreview(await previewOpenApi(source))
    } catch (error) {
      setPreview(null)
      setPreviewError(error instanceof Error ? error.message : String(error))
    } finally {
      setPreviewing(false)
    }
  }

  const handleImport = async () => {
    if (!preview) return
    setImporting(true)
    try {
      const source = sourceTab === 'paste' ? { content } : { url: url.trim() }
      const credentials = compactCredentialDrafts(credentialDraft)
      const imported = await importOpenApi(
        source,
        Object.keys(credentials).length > 0 ? credentials : undefined,
      )
      antdMessage.success(
        t('message.importSuccess', { title: imported.title, count: imported.operations.length }),
      )
      setPreview(null)
      setContent('')
      void refresh()
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : String(error))
    } finally {
      setImporting(false)
    }
  }

  const handleDelete = async (spec: ImportedSpec) => {
    try {
      await deleteOpenApiImport(spec.spec_id)
      antdMessage.success(t('message.deleted'))
      void refresh()
    } catch (error) {
      antdMessage.error(error instanceof Error ? error.message : String(error))
    }
  }

  // docs/60 G2：物理删除（仅已软删条目）
  const handlePurge = async (spec: ImportedSpec) => {
    try {
      await purgeOpenApiImport(spec.spec_id)
      antdMessage.success(t('message.purged'))
      void refresh()
    } catch (error) {
      antdMessage.error(error instanceof Error ? error.message : String(error))
    }
  }

  const openCredentials = (spec: ImportedSpec) => {
    setCredentialSpec(spec)
    setCredentialValues({})
  }

  const handleSaveCredentials = async () => {
    if (!credentialSpec) return
    setSavingCredentials(true)
    try {
      const credentials = credentialUpsertPayload(credentialValues)
      await putOpenApiCredentials(credentialSpec.spec_id, credentials)
      antdMessage.success(t('message.credentialsSaved'))
      setCredentialSpec(null)
      void refresh()
    } catch (error) {
      antdMessage.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSavingCredentials(false)
    }
  }

  const previewColumns: ColumnsType<OperationDescriptor> = [
    {
      title: t('preview.column.method'),
      dataIndex: 'method',
      width: 90,
      render: (method: string) => methodTag(method),
    },
    { title: t('preview.column.name'), dataIndex: 'name', width: 180 },
    { title: t('preview.column.path'), dataIndex: 'path' },
    {
      title: t('preview.column.summary'),
      dataIndex: 'summary',
      render: (summary: string | null, record) => summary || record.description || record.path,
    },
    {
      title: t('preview.column.permission'),
      dataIndex: 'permission',
      width: 90,
      render: (permission: string, record) =>
        record.skipped ? null : permissionTag(permission, t),
    },
    {
      title: t('preview.column.skipReason'),
      dataIndex: 'skip_reason',
      width: 220,
      render: (reason: string | null) => reason && <Tag>{reason}</Tag>,
    },
  ]

  const operationColumns: ColumnsType<OperationDescriptor> = [
    {
      title: t('imports.expand.method'),
      dataIndex: 'method',
      width: 90,
      render: (method: string) => methodTag(method),
    },
    { title: t('imports.expand.path'), dataIndex: 'path' },
    {
      title: t('imports.expand.permission'),
      dataIndex: 'permission',
      width: 100,
      render: (permission: string) => permissionTag(permission, t),
    },
  ]

  const importColumns: ColumnsType<ImportedSpec> = [
    {
      title: t('imports.column.title'),
      dataIndex: 'title',
      render: (value: string, record) => (
        <Space size={6}>
          <span>{value}</span>
          {record.deleted_at ? <Tag color="red">{t('imports.deletedTag')}</Tag> : null}
        </Space>
      ),
    },
    { title: t('imports.column.baseUrl'), dataIndex: 'base_url' },
    {
      title: t('imports.column.operations'),
      dataIndex: 'operations',
      width: 90,
      render: (ops: OperationDescriptor[]) => ops.length,
    },
    {
      title: t('imports.column.auth'),
      key: 'auth',
      width: 220,
      render: (_, record) => {
        const schemes = Object.values(record.security_schemes)
        if (schemes.length === 0) return <Tag>{record.operations.length ? '-' : ''}</Tag>
        const configured = schemes.filter(
          (scheme) => scheme.name in record.credential_envelopes,
        ).length
        return (
          <Space orientation="vertical" size={4}>
            <Space size={4} wrap>
              {schemes.map((scheme) => (
                <Tag
                  key={scheme.name}
                  color={scheme.name in record.credential_envelopes ? 'green' : 'default'}
                >
                  {scheme.name}
                </Tag>
              ))}
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {configured === 0
                ? t('credentials.none')
                : t('credentials.configured', { configured, total: schemes.length })}
            </Typography.Text>
          </Space>
        )
      },
    },
    {
      title: t('imports.column.createdAt'),
      dataIndex: 'created_at',
      width: 190,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: t('imports.column.actions'),
      key: 'actions',
      width: 180,
      render: (_, record) =>
        canAdmin ? (
          <Space>
            {record.deleted_at ? (
              <Popconfirm
                title={t('purge.confirmTitle')}
                description={t('purge.confirm', { title: record.title })}
                okText={t('button.purge')}
                okButtonProps={{ danger: true }}
                cancelText={t('common:button.cancel')}
                onConfirm={() => handlePurge(record)}
              >
                <Button danger size="small">
                  {t('button.purge')}
                </Button>
              </Popconfirm>
            ) : (
              <>
                <Button size="small" onClick={() => openCredentials(record)}>
                  {t('button.configure')}
                </Button>
                <Popconfirm
                  title={t('delete.confirmTitle')}
                  description={t('delete.confirm', { title: record.title })}
                  okText={t('button.delete')}
                  okButtonProps={{ danger: true }}
                  cancelText={t('common:button.cancel')}
                  onConfirm={() => handleDelete(record)}
                >
                  <Button danger size="small">
                    {t('button.delete')}
                  </Button>
                </Popconfirm>
              </>
            )}
          </Space>
        ) : null,
    },
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={onBack}>{t('button.back')}</Button>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('title')}
          </Typography.Title>
        </Space>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Typography.Paragraph>{t('subtitle')}</Typography.Paragraph>
          {!canOperate && <Alert type="info" showIcon message={t('readonly.note')} />}

          {canOperate && (
            <Card>
              <Tabs
                activeKey={sourceTab}
                onChange={(key) => setSourceTab(key as 'paste' | 'url')}
                items={[
                  {
                    key: 'paste',
                    label: t('source.pasteTab'),
                    children: (
                      <Input.TextArea
                        rows={10}
                        value={content}
                        placeholder={t('source.pastePlaceholder')}
                        onChange={(event) => setContent(event.target.value)}
                      />
                    ),
                  },
                  {
                    key: 'url',
                    label: t('source.urlTab'),
                    children: (
                      <Input
                        value={url}
                        placeholder={t('source.urlPlaceholder')}
                        onChange={(event) => setUrl(event.target.value)}
                      />
                    ),
                  },
                ]}
              />
              {previewError && (
                <Alert type="error" showIcon style={{ marginBottom: 12 }} message={previewError} />
              )}
              <Button type="primary" loading={previewing} onClick={handlePreview}>
                {t('button.preview')}
              </Button>
            </Card>
          )}

          {preview && (
            <Card
              title={t('preview.title')}
              extra={
                <Button
                  type="primary"
                  loading={importing}
                  disabled={preview.imported_count === 0}
                  onClick={handleImport}
                >
                  {t('button.import')}
                </Button>
              }
            >
              <Typography.Paragraph>
                {t('preview.summary', {
                  title: preview.title,
                  baseUrl: preview.base_url,
                  total: preview.operations.length,
                  imported: preview.imported_count,
                  skipped: preview.skipped_count,
                })}
              </Typography.Paragraph>
              <Table
                rowKey="name"
                size="small"
                pagination={false}
                columns={previewColumns}
                dataSource={preview.operations}
                rowClassName={(record) => (record.skipped ? 'openapi-row-skipped' : '')}
              />
              <CredentialFields
                schemes={preview.security_schemes}
                values={credentialDraft}
                t={t}
                onChange={(name, value) =>
                  setCredentialDraft((current) => ({ ...current, [name]: value }))
                }
              />
            </Card>
          )}

          <Card
            title={t('imports.title')}
            extra={
              canAdmin ? (
                <Space>
                  <Switch
                    size="small"
                    checked={showDeleted}
                    onChange={setShowDeleted}
                  />
                  <Typography.Text type="secondary">
                    {t('imports.showDeleted')}
                  </Typography.Text>
                </Space>
              ) : undefined
            }
          >
            {listError && (
              <Alert type="error" showIcon style={{ marginBottom: 12 }} message={listError} />
            )}
            <Table
              rowKey="spec_id"
              loading={loading}
              pagination={false}
              columns={importColumns}
              dataSource={items}
              expandable={{
                expandedRowRender: (record) => (
                  <Table
                    rowKey="name"
                    size="small"
                    pagination={false}
                    columns={operationColumns}
                    dataSource={record.operations}
                  />
                ),
                rowExpandable: (record) => record.operations.length > 0,
              }}
              locale={{ emptyText: t('imports.empty') }}
            />
          </Card>
        </Space>
      </Content>

      <Modal
        title={t('credentials.title')}
        open={credentialSpec !== null}
        confirmLoading={savingCredentials}
        okText={t('credentials.save')}
        cancelText={t('common:button.cancel')}
        onOk={handleSaveCredentials}
        onCancel={() => setCredentialSpec(null)}
      >
        {credentialSpec && (
          <>
            <Alert type="info" showIcon style={{ marginBottom: 12 }} message={t('credentials.hint')} />
            <CredentialFields
              schemes={Object.values(credentialSpec.security_schemes)}
              values={credentialValues}
              t={t}
              onChange={(name, value) =>
                setCredentialValues((current) => ({ ...current, [name]: value }))
              }
            />
          </>
        )}
      </Modal>
    </Layout>
  )
}
