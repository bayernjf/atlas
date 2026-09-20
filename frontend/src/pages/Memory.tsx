import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Layout,
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
  deleteMemory,
  listMemories,
  searchMemories,
  type MemoryItem,
  type MemoryKind,
  type MemorySearchResult,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import {
  formatConfidence,
  formatCreatedAt,
  formatScope,
  formatScore,
  kindColor,
  kindLabel,
} from '../lib/memory'

const { Content, Header } = Layout

type MemoryProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

type KindFilter = MemoryKind | 'all'

export function Memory({ principal, onLogout, onBack }: MemoryProps) {
  const canAdmin = roleCan(principal.role, 'administer')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [listKind, setListKind] = useState<KindFilter>('all')
  const [loadingList, setLoadingList] = useState(true)
  const [listError, setListError] = useState('')

  const [query, setQuery] = useState('')
  const [searchKind, setSearchKind] = useState<KindFilter>('all')
  const [results, setResults] = useState<MemorySearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const data = await listMemories(listKind === 'all' ? undefined : listKind)
      setItems(data)
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : '加载记忆失败')
    } finally {
      setLoadingList(false)
    }
  }, [listKind])

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
      setSearchError('请输入检索内容')
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
      setSearchError(error instanceof Error ? error.message : '检索失败')
    } finally {
      setSearching(false)
    }
  }, [query, searchKind])

  const handleDelete = useCallback(
    async (id: string) => {
      await deleteMemory(id)
      // 删除后同步列表与搜索结果
      await refresh()
      setResults((prev) => (prev === null ? prev : prev.filter((item) => item.id !== id)))
    },
    [refresh],
  )

  const listColumns: ColumnsType<MemoryItem> = [
    {
      title: '内容',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'kind',
      key: 'kind',
      width: 90,
      render: (kind: MemoryKind) => <Tag color={kindColor(kind)}>{kindLabel(kind)}</Tag>,
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 90,
      render: (value: number) => formatConfidence(value),
    },
    {
      title: '作用域',
      dataIndex: 'scope',
      key: 'scope',
      width: 200,
      render: (scope: Record<string, string>) =>
        formatScope(scope) || <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (value: string) => formatCreatedAt(value),
    },
    ...(canAdmin
      ? [
          {
            title: '操作',
            key: 'actions',
            width: 90,
            render: (_: unknown, record: MemoryItem) => (
              <Popconfirm
                title="删除该条记忆？"
                description="删除后不可恢复，且不影响已结束的运行。"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(record.id)}
              >
                <Button size="small" danger>
                  删除
                </Button>
              </Popconfirm>
            ),
          } as ColumnsType<MemoryItem>[number],
        ]
      : []),
  ]

  const searchColumns: ColumnsType<MemorySearchResult> = [
    {
      title: '内容',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'kind',
      key: 'kind',
      width: 90,
      render: (kind: MemoryKind) => <Tag color={kindColor(kind)}>{kindLabel(kind)}</Tag>,
    },
    {
      title: '相似度',
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
      title: '作用域',
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
            长期记忆
          </Typography.Title>
          <Space>
            <Button onClick={handleManualRefresh}>刷新</Button>
            <Button onClick={onBack}>返回 Dashboard</Button>
            <UserBadge principal={principal} onLogout={onLogout} />
          </Space>
        </Space>
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="本地词法向量，用于机制演示，非真实语义"
            description="相似度由内置确定性词法向量计算（离线可复现），不代表商业语义模型效果；记忆写入只发生在流程运行中。"
          />

          <Card
            title="语义搜索"
            extra={
              <Select<KindFilter>
                value={searchKind}
                onChange={setSearchKind}
                style={{ width: 120 }}
                options={[
                  { value: 'all', label: '全部类型' },
                  { value: 'fact', label: '事实' },
                  { value: 'preference', label: '偏好' },
                ]}
              />
            }
          >
            <Space.Compact style={{ width: '100%' }}>
              <Input
                placeholder="输入自然语言，如：客户的配送偏好"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onPressEnter={() => void runSearch()}
                allowClear
              />
              <Button type="primary" loading={searching} onClick={() => void runSearch()}>
                搜索
              </Button>
            </Space.Compact>
            {searchError && (
              <Alert type="error" showIcon message={searchError} style={{ marginTop: 12 }} />
            )}
            {results !== null && !searchError && (
              <div style={{ marginTop: 16 }}>
                {results.length === 0 ? (
                  <Empty description="无匹配记忆" image={Empty.PRESENTED_IMAGE_SIMPLE} />
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
            title="记忆列表"
            extra={
              <Space>
                <Select<KindFilter>
                  value={listKind}
                  onChange={handleListKindChange}
                  style={{ width: 120 }}
                  options={[
                    { value: 'all', label: '全部类型' },
                    { value: 'fact', label: '事实' },
                    { value: 'preference', label: '偏好' },
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
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无记忆" /> }}
            />
          </Card>
        </Space>
      </Content>
    </Layout>
  )
}
