import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Input,
  Layout,
  Popconfirm,
  Space,
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
  importOpenApi,
  listOpenApiImports,
  previewOpenApi,
  type ImportedSpec,
  type OpenApiPreview,
  type OperationDescriptor,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { formatDateTime } from '../lib/connections'
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

  const [items, setItems] = useState<ImportedSpec[]>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')

  const refresh = useCallback(async () => {
    try {
      setItems(await listOpenApiImports())
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('imports.title'))
    } finally {
      setLoading(false)
    }
  }, [t])

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
      const imported = await importOpenApi(source)
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
    { title: t('imports.column.title'), dataIndex: 'title' },
    { title: t('imports.column.baseUrl'), dataIndex: 'base_url' },
    {
      title: t('imports.column.operations'),
      dataIndex: 'operations',
      width: 90,
      render: (ops: OperationDescriptor[]) => ops.length,
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
      width: 90,
      render: (_, record) =>
        canAdmin ? (
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
            </Card>
          )}

          <Card title={t('imports.title')}>
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
    </Layout>
  )
}
