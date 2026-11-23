import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  deleteMemory,
  importKnowledge,
  listMemories,
  type KnowledgeCategory,
  type MemoryItem,
  type MemoryKind,
} from '../lib/apiClient'
import { KNOWLEDGE_CATEGORIES, kindColor, kindLabel, formatCreatedAt } from '../lib/memory'
import { useTranslation } from '../locales'

const { TextArea } = Input

type KnowledgePanelProps = {
  canOperate: boolean
  canAdmin: boolean
  onMutated: () => void
}

type CategoryFilter = KnowledgeCategory | 'all'

/** 知识列表单条按分类取值（kind=knowledge 时 meta.category 恒在白名单）。 */
function categoryOf(item: MemoryItem): string {
  return item.metadata?.category ?? ''
}

const KNOWLEDGE_FILE_ACCEPT = '.md,.markdown,.txt,.docx'
const KNOWLEDGE_FILE_MAX = 10 * 1024 * 1024
const KNOWLEDGE_FILE_EXTENSIONS = ['.md', '.markdown', '.txt', '.docx']

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result ?? '')
      const payload = dataUrl.split(',', 2)[1]
      if (!payload) reject(new Error('empty file'))
      else resolve(payload)
    }
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(file)
  })
}

export function KnowledgePanel({ canOperate, canAdmin, onMutated }: KnowledgePanelProps) {
  const { t } = useTranslation('memory')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [filter, setFilter] = useState<CategoryFilter>('all')
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')

  const [importCategory, setImportCategory] = useState<KnowledgeCategory>('faq')
  const [importText, setImportText] = useState('')
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState('')
  const [importNotice, setImportNotice] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await listMemories('knowledge', 200)
      setItems(data)
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('error.loadList'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  const handleFileChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0] ?? null
      setImportError('')
      if (!file) {
        setImportFile(null)
        return
      }
      const lower = file.name.toLowerCase()
      if (!KNOWLEDGE_FILE_EXTENSIONS.some((suffix) => lower.endsWith(suffix))) {
        setImportFile(null)
        setImportError(t('knowledge.importFileTypeError'))
        event.target.value = ''
        return
      }
      if (file.size > KNOWLEDGE_FILE_MAX) {
        setImportFile(null)
        setImportError(t('knowledge.importFileTooLarge'))
        event.target.value = ''
        return
      }
      setImportFile(file)
    },
    [t],
  )

  const clearSelectedFile = useCallback(() => {
    setImportFile(null)
    setImportError('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [])

  const handleImport = useCallback(async () => {
    const text = importText.trim()
    if (!text && !importFile) {
      setImportError(t('knowledge.importTextOrFileRequired'))
      return
    }
    setImporting(true)
    setImportError('')
    setImportNotice('')
    try {
      let payload: Parameters<typeof importKnowledge>[0]
      if (importFile) {
        const content_base64 = await fileToBase64(importFile)
        payload = { category: importCategory, filename: importFile.name, content_base64 }
      } else {
        payload = { category: importCategory, text }
      }
      const result = await importKnowledge(payload)
      setImportNotice(
        t('knowledge.importSuccess', { imported: result.imported }) +
          (result.truncated ? t('knowledge.importTruncated') : ''),
      )
      setImportText('')
      clearSelectedFile()
      await refresh()
      onMutated()
    } catch (error) {
      setImportError(error instanceof Error ? error.message : t('knowledge.importError'))
    } finally {
      setImporting(false)
    }
  }, [clearSelectedFile, importCategory, importFile, importText, onMutated, refresh, t])

  const handleDelete = useCallback(
    async (id: string) => {
      await deleteMemory(id)
      await refresh()
      onMutated()
    },
    [onMutated, refresh],
  )

  const visible = filter === 'all' ? items : items.filter((item) => categoryOf(item) === filter)

  const columns: ColumnsType<MemoryItem> = [
    {
      title: t('col.content'),
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: t('knowledge.categoryColumn'),
      key: 'category',
      width: 120,
      render: (_: unknown, record: MemoryItem) => {
        const category = categoryOf(record)
        return category ? <Tag color="cyan">{t(`category.${category}`)}</Tag> : <Typography.Text type="secondary">—</Typography.Text>
      },
    },
    {
      title: t('knowledge.sourceColumn'),
      key: 'source',
      width: 180,
      render: (_: unknown, record: MemoryItem) => {
        const source = record.metadata?.source
        const chunk = record.metadata?.chunk_index
        return source ? (
          <Typography.Text ellipsis style={{ maxWidth: 160 }}>
            {source}
            {chunk !== undefined ? ` #${chunk}` : ''}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        )
      },
    },
    {
      title: t('col.kind'),
      dataIndex: 'kind',
      key: 'kind',
      width: 90,
      render: (kind: MemoryKind) => <Tag color={kindColor(kind)}>{t(kindLabel(kind))}</Tag>,
    },
    {
      title: t('col.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (value: string) => formatCreatedAt(value),
    },
    ...(canAdmin
      ? [
          {
            title: t('col.actions'),
            key: 'actions',
            width: 84,
            render: (_: unknown, record: MemoryItem) => (
              <Popconfirm
                title={t('deleteConfirm.title')}
                description={t('deleteConfirm.description')}
                okText={t('common:button.delete')}
                cancelText={t('common:button.cancel')}
                okButtonProps={{ danger: true }}
                onConfirm={() => void handleDelete(record.id)}
              >
                <Button size="small" danger>
                  {t('common:button.delete')}
                </Button>
              </Popconfirm>
            ),
          } as ColumnsType<MemoryItem>[number],
        ]
      : []),
  ]

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message={t('notice.message')}
        description={t('knowledge.importHint')}
      />

      {canOperate && (
        <Card title={t('knowledge.importCardTitle')}>
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Space wrap>
              <Typography.Text>{t('knowledge.importCategoryLabel')}</Typography.Text>
              <Select<KnowledgeCategory>
                value={importCategory}
                onChange={setImportCategory}
                aria-label={t('knowledge.importCategoryLabel')}
                style={{ width: 160 }}
                options={KNOWLEDGE_CATEGORIES.map((category) => ({
                  value: category,
                  label: t(`category.${category}`),
                }))}
              />
            </Space>
            <TextArea
              rows={5}
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              placeholder={t('knowledge.importTextPlaceholder')}
              aria-label={t('knowledge.importCategoryLabel')}
              style={{ fontFamily: 'inherit' }}
            />
            <Space wrap>
              <input
                ref={fileInputRef}
                type="file"
                accept={KNOWLEDGE_FILE_ACCEPT}
                onChange={handleFileChange}
                style={{ display: 'none' }}
                aria-label={t('knowledge.importFileLabel')}
              />
              <Button onClick={() => fileInputRef.current?.click()}>
                {t('knowledge.importFileChoose')}
              </Button>
              {importFile ? (
                <>
                  <Typography.Text>
                    {t('knowledge.importFileSelected', { name: importFile.name })}
                  </Typography.Text>
                  <Button size="small" onClick={clearSelectedFile}>
                    {t('knowledge.importFileClear')}
                  </Button>
                </>
              ) : (
                <Typography.Text type="secondary">
                  {t('knowledge.importFileLabel')}
                </Typography.Text>
              )}
            </Space>
            {importError && <Alert type="error" showIcon message={importError} />}
            {importNotice && <Alert type="success" showIcon message={importNotice} />}
            <Button type="primary" loading={importing} onClick={() => void handleImport()}>
              {importing ? t('knowledge.importing') : t('knowledge.importButton')}
            </Button>
          </Space>
        </Card>
      )}

      <Card
        title={t('knowledge.listTitle')}
        extra={
          <Select<CategoryFilter>
            value={filter}
            onChange={setFilter}
            aria-label={t('knowledge.importCategoryLabel')}
            style={{ width: 160 }}
            options={[
              { value: 'all', label: t('category.all') },
              ...KNOWLEDGE_CATEGORIES.map((category) => ({
                value: category,
                label: t(`category.${category}`),
              })),
            ]}
          />
        }
      >
        {listError && <Alert type="error" showIcon message={listError} style={{ marginBottom: 12 }} />}
        <Table<MemoryItem>
          rowKey="id"
          columns={columns}
          dataSource={visible}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          size="small"
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('knowledge.listEmpty')} /> }}
        />
      </Card>
    </Space>
  )
}
