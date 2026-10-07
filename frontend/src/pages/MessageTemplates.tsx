import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  App,
  Button,
  Card,
  Form,
  Input,
  Layout,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createMessageTemplate,
  deleteMessageTemplate,
  listMessageTemplates,
  updateMessageTemplate,
  type MessageTemplateInput,
  type MessageTemplateKind,
  type MessageTemplateSummary,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import {
  MESSAGE_TEMPLATE_KINDS,
  MESSAGE_TEMPLATE_KIND_LABELS,
  MAX_VARIABLES,
  parseVariablesInput,
  validateMessageTemplate,
  validateVariables,
} from '../lib/messageTemplate'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type MessageTemplatesPageProps = {
  principal: Principal
  onBack: () => void
}

const KIND_COLORS: Record<MessageTemplateKind, string> = {
  approval: 'blue',
  alert: 'gold',
}

type EditorState = {
  id: string | null
  name: string
  kind: MessageTemplateKind
  subject: string
  body: string
  variablesText: string
}

const EMPTY_EDITOR: EditorState = {
  id: null,
  name: '',
  kind: 'approval',
  subject: '',
  body: '',
  variablesText: '',
}

export function MessageTemplates({ principal, onBack }: MessageTemplatesPageProps) {
  const { t } = useTranslation('templates')
  const { message } = App.useApp()
  const [items, setItems] = useState<MessageTemplateSummary[]>([])
  const [kindFilter, setKindFilter] = useState<MessageTemplateKind | undefined>(undefined)
  const [loading, setLoading] = useState(true)
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await listMessageTemplates(kindFilter))
      setLoadError(null)
    } catch {
      setLoadError(t('loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [kindFilter, t])

  useEffect(() => {
    void load()
  }, [load])

  const variables = useMemo(
    () => (editor ? parseVariablesInput(editor.variablesText) : []),
    [editor],
  )
  const editorErrors = useMemo(() => {
    if (!editor) {
      return []
    }
    return validateMessageTemplate({
      name: editor.name,
      kind: editor.kind,
      subject: editor.subject,
      body: editor.body,
      variables,
    })
  }, [editor, variables])

  const canOperate = roleCan(principal.role, 'operate')

  const openCreate = () => {
    setEditor({ ...EMPTY_EDITOR })
  }

  const openEdit = (item: MessageTemplateSummary) => {
    // 列表投影不含 body，编辑时拉详情。
    setEditor({
      id: item.id,
      name: item.name,
      kind: item.kind,
      subject: item.subject,
      body: '',
      variablesText: item.variables.join(', '),
    })
  }

  const save = async () => {
    if (!editor) {
      return
    }
    setSaving(true)
    const input: MessageTemplateInput = {
      name: editor.name.trim(),
      kind: editor.kind,
      subject: editor.subject,
      body: editor.body,
      variables,
    }
    try {
      if (editor.id === null) {
        await createMessageTemplate(input)
      } else {
        await updateMessageTemplate(editor.id, input)
      }
      message.success(t('saved'))
      setEditor(null)
      await load()
    } catch (error) {
      const detail =
        typeof error === 'object' && error !== null && 'detail' in error
          ? String((error as { detail: unknown }).detail)
          : ''
      if (detail.includes('已存在')) {
        message.error(t('nameConflict'))
      } else {
        message.error(`${t('saveFailed')}${detail ? `：${detail}` : ''}`)
      }
    } finally {
      setSaving(false)
    }
  }

  const remove = async (id: string) => {
    try {
      await deleteMessageTemplate(id)
      message.success(t('deleted'))
      await load()
    } catch {
      message.error(t('saveFailed'))
    }
  }

  const columns: ColumnsType<MessageTemplateSummary> = [
    {
      title: t('name'),
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <Typography.Text strong>{name}</Typography.Text>,
    },
    {
      title: t('kind'),
      dataIndex: 'kind',
      key: 'kind',
      width: 120,
      render: (kind: MessageTemplateKind) => (
        <Tag color={KIND_COLORS[kind]}>{MESSAGE_TEMPLATE_KIND_LABELS[kind]}</Tag>
      ),
    },
    {
      title: t('subject'),
      dataIndex: 'subject',
      key: 'subject',
      render: (subject: string) => <Typography.Text code>{subject}</Typography.Text>,
    },
    {
      title: t('variables'),
      dataIndex: 'variables',
      key: 'variables',
      render: (vars: string[]) =>
        vars.length === 0 ? (
          <Typography.Text type="secondary">-</Typography.Text>
        ) : (
          <Space size={[4, 4]} wrap>
            {vars.map((variable) => (
              <Tag key={variable}>{variable}</Tag>
            ))}
          </Space>
        ),
    },
    {
      title: t('updatedAt'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 190,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    ...(canOperate
      ? [
          {
            title: t('actions'),
            key: 'actions',
            width: 150,
            render: (_: unknown, record: MessageTemplateSummary) => (
              <Space>
                <Button size="small" onClick={() => openEdit(record)}>
                  {t('edit')}
                </Button>
                <Popconfirm
                  title={t('deleteConfirm')}
                  onConfirm={() => void remove(record.id)}
                >
                  <Button size="small" danger>
                    {t('delete')}
                  </Button>
                </Popconfirm>
              </Space>
            ),
          } satisfies ColumnsType<MessageTemplateSummary>[number],
        ]
      : []),
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Button onClick={onBack}>←</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t('title')}
        </Typography.Title>
      </Header>
      <Content style={{ padding: 24 }}>
        <Card>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
              {t('subtitle')}
            </Typography.Paragraph>
            <Space wrap>
              <Select
                style={{ width: 160 }}
                value={kindFilter}
                allowClear
                placeholder={t('kindAll')}
                onChange={(value: MessageTemplateKind | undefined) => setKindFilter(value)}
                options={MESSAGE_TEMPLATE_KINDS.map((kind) => ({
                  value: kind,
                  label: MESSAGE_TEMPLATE_KIND_LABELS[kind],
                }))}
              />
              {canOperate && (
                <Button type="primary" onClick={openCreate}>
                  {t('create')}
                </Button>
              )}
            </Space>
            {loadError && <Typography.Text type="danger">{loadError}</Typography.Text>}
            <Table
              rowKey="id"
              loading={loading}
              dataSource={items}
              columns={columns}
              pagination={false}
              size="middle"
            />
          </Space>
        </Card>
      </Content>

      <Modal
        open={editor !== null}
        title={editor?.id === null ? t('create') : t('edit')}
        okText={t('save')}
        cancelText={t('cancel')}
        confirmLoading={saving}
        onOk={save}
        onCancel={() => setEditor(null)}
        okButtonProps={{ disabled: editorErrors.length > 0 }}
        width={640}
      >
        {editor !== null && (
          <Form layout="vertical">
            <Form.Item label={t('name')} required>
              <Input
                value={editor.name}
                maxLength={64}
                onChange={(event) => setEditor({ ...editor, name: event.target.value })}
              />
            </Form.Item>
            <Form.Item label={t('kind')} required>
              <Select
                value={editor.kind}
                onChange={(kind: MessageTemplateKind) => setEditor({ ...editor, kind })}
                options={MESSAGE_TEMPLATE_KINDS.map((kind) => ({
                  value: kind,
                  label: MESSAGE_TEMPLATE_KIND_LABELS[kind],
                }))}
              />
            </Form.Item>
            <Form.Item label={t('subject')} required>
              <Input
                value={editor.subject}
                maxLength={200}
                placeholder="{{title}}"
                onChange={(event) => setEditor({ ...editor, subject: event.target.value })}
              />
            </Form.Item>
            <Form.Item label={t('body')} required>
              <Input.TextArea
                value={editor.body}
                maxLength={4000}
                autoSize={{ minRows: 5, maxRows: 10 }}
                onChange={(event) => setEditor({ ...editor, body: event.target.value })}
              />
            </Form.Item>
            <Form.Item
              label={t('variables')}
              help={t('variablesHint')}
              extra={
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t('variablesHelp')}
                </Typography.Text>
              }
            >
              <Input
                value={editor.variablesText}
                onChange={(event) =>
                  setEditor({ ...editor, variablesText: event.target.value })
                }
              />
            </Form.Item>
            {editorErrors.length > 0 && (
              <Form.Item>
                {editorErrors.map((error) => (
                  <Typography.Text key={error} type="danger" style={{ display: 'block' }}>
                    {error}
                  </Typography.Text>
                ))}
              </Form.Item>
            )}
            {validateVariables(variables).length > 0 && (
              <Typography.Text type="warning">
                {t('variablesHint')}（{variables.length}/{MAX_VARIABLES}）
              </Typography.Text>
            )}
          </Form>
        )}
      </Modal>
    </Layout>
  )
}
