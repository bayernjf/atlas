import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Layout,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createMemory,
  deleteMemory,
  listMemories,
  searchMemories,
  updateMemory,
  type MemoryItem,
  type MemoryKind,
  type MemorySearchResult,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import {
  buildMemoryPayload,
  formatConfidence,
  formatCreatedAt,
  formatScope,
  formatScore,
  kindColor,
  kindLabel,
  type MemoryDraft,
} from '../lib/memory'
import { useTranslation } from '../locales'

const { TextArea } = Input
const { Content, Header } = Layout

type MemoryProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

type KindFilter = MemoryKind | 'all'

const EMPTY_DRAFT: MemoryDraft = {
  kind: 'fact',
  content: '',
  confidence: 1,
  scopeText: '',
  metadataText: '',
}

export function Memory({ principal, onLogout, onBack }: MemoryProps) {
  const { t } = useTranslation('memory')
  const canAdmin = roleCan(principal.role, 'administer')
  const canOperate = roleCan(principal.role, 'operate')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [listKind, setListKind] = useState<KindFilter>('all')
  const [loadingList, setLoadingList] = useState(true)
  const [listError, setListError] = useState('')

  const [query, setQuery] = useState('')
  const [searchKind, setSearchKind] = useState<KindFilter>('all')
  const [results, setResults] = useState<MemorySearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  // ⑩ 手动新建/编辑（operate；source 由后端固定 manual）
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<MemoryItem | null>(null)
  const [draft, setDraft] = useState<MemoryDraft>(EMPTY_DRAFT)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const data = await listMemories(listKind === 'all' ? undefined : listKind)
      setItems(data)
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('error.loadList'))
    } finally {
      setLoadingList(false)
    }
  }, [listKind, t])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  const handleManualRefresh = useCallback(() => {
    setLoadingList(true)
    setListError('')
    void refresh()
  }, [refresh])

  const handleListKindChange = useCallback((kind: KindFilter) => {
    setListKind(kind)
    setLoadingList(true)
    setListError('')
  }, [])

  const runSearch = useCallback(async () => {
    const q = query.trim()
    if (!q) {
      setSearchError(t('search.queryRequired'))
      return
    }
    setSearching(true)
    setSearchError('')
    try {
      const data = await searchMemories(q, {
        kind: searchKind === 'all' ? undefined : searchKind,
        topK: 5,
      })
      setResults(data)
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : t('error.search'))
    } finally {
      setSearching(false)
    }
  }, [query, searchKind, t])

  const handleDelete = useCallback(
    async (id: string) => {
      await deleteMemory(id)
      // 删除后同步列表与搜索结果
      await refresh()
      setResults((prev) => (prev === null ? prev : prev.filter((item) => item.id !== id)))
    },
    [refresh],
  )

  const patchDraft = useCallback((patch: Partial<MemoryDraft>) => {
    setDraft((prev) => ({ ...prev, ...patch }))
  }, [])

  const openCreate = useCallback(() => {
    setEditing(null)
    setDraft(EMPTY_DRAFT)
    setFormError('')
    setEditorOpen(true)
  }, [])

  const openEdit = useCallback((record: MemoryItem) => {
    setEditing(record)
    setDraft({
      kind: record.kind,
      content: record.content,
      confidence: record.confidence,
      scopeText: Object.keys(record.scope).length ? JSON.stringify(record.scope, null, 2) : '',
      metadataText: Object.keys(record.metadata).length
        ? JSON.stringify(record.metadata, null, 2)
        : '',
    })
    setFormError('')
    setEditorOpen(true)
  }, [])

  const draftValid = Boolean(buildMemoryPayload(draft).payload)

  const handleSave = useCallback(async () => {
    const { payload, error } = buildMemoryPayload(draft)
    if (!payload) {
      setFormError(error ?? t('error.formInvalid'))
      return
    }
    setSaving(true)
    setFormError('')
    try {
      if (editing) {
        await updateMemory(editing.id, payload)
      } else {
        await createMemory(payload)
      }
      setEditorOpen(false)
      await refresh()
    } catch (saveError) {
      setFormError(saveError instanceof Error ? saveError.message : t('error.save'))
    } finally {
      setSaving(false)
    }
  }, [draft, editing, refresh, t])

  const listColumns: ColumnsType<MemoryItem> = [
    {
      title: t('col.content'),
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: t('col.kind'),
      dataIndex: 'kind',
      key: 'kind',
      width: 90,
      render: (kind: MemoryKind) => <Tag color={kindColor(kind)}>{t(kindLabel(kind))}</Tag>,
    },
    {
      title: t('col.confidence'),
      dataIndex: 'confidence',
      key: 'confidence',
      width: 90,
      render: (value: number) => formatConfidence(value),
    },
    {
      title: t('col.scope'),
      dataIndex: 'scope',
      key: 'scope',
      width: 200,
      render: (scope: Record<string, string>) =>
        formatScope(scope) || <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: t('col.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (value: string) => formatCreatedAt(value),
    },
    ...(canOperate
      ? [
          {
            title: t('col.actions'),
            key: 'actions',
            width: canAdmin ? 132 : 84,
            render: (_: unknown, record: MemoryItem) => (
              <Space size={4}>
                <Button size="small" onClick={() => openEdit(record)}>
                  {t('button.edit')}
                </Button>
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
          } as ColumnsType<MemoryItem>[number],
        ]
      : []),
  ]

  const searchColumns: ColumnsType<MemorySearchResult> = [
    {
      title: t('col.content'),
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: t('col.kind'),
      dataIndex: 'kind',
      key: 'kind',
      width: 90,
      render: (kind: MemoryKind) => <Tag color={kindColor(kind)}>{t(kindLabel(kind))}</Tag>,
    },
    {
      title: t('col.score'),
      dataIndex: 'score',
      key: 'score',
      width: 200,
      render: (score: number) => (
        <Space size="small">
          <Progress percent={Math.round(Math.max(0, Math.min(1, score)) * 100)} size="small" style={{ width: 120 }} />
          <Typography.Text strong>{formatScore(score)}</Typography.Text>
        </Space>
      ),
    },
    {
      title: t('col.scope'),
      dataIndex: 'scope',
      key: 'scope',
      width: 180,
      render: (scope: Record<string, string>) =>
        formatScope(scope) || <Typography.Text type="secondary">—</Typography.Text>,
    },
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header">
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('title')}
          </Typography.Title>
          <Space>
            <Button onClick={handleManualRefresh}>{t('header.refresh')}</Button>
            <Button onClick={onBack}>{t('header.back')}</Button>
            <UserBadge principal={principal} onLogout={onLogout} />
          </Space>
        </Space>
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message={t('notice.message')}
            description={t('notice.description')}
          />

          <Card
            title={t('search.cardTitle')}
            extra={
              <Select<KindFilter>
                value={searchKind}
                onChange={setSearchKind}
                style={{ width: 120 }}
                options={[
                  { value: 'all', label: t('filter.allKinds') },
                  { value: 'fact', label: t('kind.fact') },
                  { value: 'preference', label: t('kind.preference') },
                ]}
              />
            }
          >
            <Space.Compact style={{ width: '100%' }}>
              <Input
                placeholder={t('search.placeholder')}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onPressEnter={() => void runSearch()}
                allowClear
              />
              <Button type="primary" loading={searching} onClick={() => void runSearch()}>
                {t('button.search')}
              </Button>
            </Space.Compact>
            {searchError && (
              <Alert type="error" showIcon message={searchError} style={{ marginTop: 12 }} />
            )}
            {results !== null && !searchError && (
              <div style={{ marginTop: 16 }}>
                {results.length === 0 ? (
                  <Empty description={t('search.empty')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
                ) : (
                  <Table<MemorySearchResult>
                    rowKey="id"
                    columns={searchColumns}
                    dataSource={results}
                    pagination={false}
                    size="small"
                  />
                )}
              </div>
            )}
          </Card>

          <Card
            title={t('list.cardTitle')}
            extra={
              <Space>
                {canOperate && (
                  <Button type="primary" onClick={openCreate}>
                    {t('list.create')}
                  </Button>
                )}
                <Select<KindFilter>
                  value={listKind}
                  onChange={handleListKindChange}
                  style={{ width: 120 }}
                  options={[
                    { value: 'all', label: t('filter.allKinds') },
                    { value: 'fact', label: t('kind.fact') },
                    { value: 'preference', label: t('kind.preference') },
                  ]}
                />
              </Space>
            }
          >
            {listError && <Alert type="error" showIcon message={listError} style={{ marginBottom: 12 }} />}
            <Table<MemoryItem>
              rowKey="id"
              columns={listColumns}
              dataSource={items}
              loading={loadingList}
              pagination={{ pageSize: 10, showSizeChanger: false }}
              size="small"
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('list.empty')} /> }}
            />
          </Card>
        </Space>
      </Content>

      <Modal
        title={editing ? t('modal.editTitle') : t('modal.createTitle')}
        open={editorOpen}
        onCancel={() => setEditorOpen(false)}
        onOk={() => void handleSave()}
        confirmLoading={saving}
        okText={t('common:button.save')}
        cancelText={t('common:button.cancel')}
        okButtonProps={{ disabled: !draftValid }}
        destroyOnClose
      >
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          {formError && <Alert type="error" showIcon message={formError} />}
          <Alert type="info" showIcon message={t('modal.sourceHint')} />
          <div>
            <Typography.Text type="secondary">{t('modal.kindLabel')}</Typography.Text>
            <Select<MemoryKind>
              value={draft.kind}
              onChange={(kind) => patchDraft({ kind })}
              style={{ width: '100%', marginTop: 4 }}
              options={[
                { value: 'fact', label: t('kind.fact') },
                { value: 'preference', label: t('kind.preference') },
              ]}
            />
          </div>
          <div>
            <Typography.Text type="secondary">{t('modal.contentLabel')}</Typography.Text>
            <TextArea
              rows={3}
              maxLength={2000}
              showCount
              value={draft.content}
              onChange={(event) => patchDraft({ content: event.target.value })}
              placeholder={t('modal.contentPlaceholder')}
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Typography.Text type="secondary">{t('modal.confidenceLabel')}</Typography.Text>
            <InputNumber
              min={0}
              max={1}
              step={0.1}
              value={draft.confidence}
              onChange={(value) => patchDraft({ confidence: value === null ? 1 : Number(value) })}
              style={{ width: '100%', marginTop: 4 }}
            />
          </div>
          <div>
            <Typography.Text type="secondary">{t('modal.scopeLabel')}</Typography.Text>
            <TextArea
              rows={2}
              value={draft.scopeText}
              onChange={(event) => patchDraft({ scopeText: event.target.value })}
              placeholder='{"user_id":"u-1"}'
              style={{ marginTop: 4, fontFamily: 'monospace' }}
            />
          </div>
          <div>
            <Typography.Text type="secondary">{t('modal.metadataLabel')}</Typography.Text>
            <TextArea
              rows={2}
              value={draft.metadataText}
              onChange={(event) => patchDraft({ metadataText: event.target.value })}
              placeholder='{"note":"手动补充"}'
              style={{ marginTop: 4, fontFamily: 'monospace' }}
            />
          </div>
        </Space>
      </Modal>
    </Layout>
  )
}
