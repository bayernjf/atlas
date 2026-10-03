import { useCallback, useEffect, useState } from 'react'
import {
  App,
  Button,
  Card,
  Collapse,
  Empty,
  Layout,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UserBadge } from '../components/UserBadge'
import { useTranslation } from '../locales'
import {
  filterReportsByGraph,
  gotoTargetFor,
  isEmptyReports,
  loadCandidate,
  loadGraphOptions,
  loadReports,
  scopeForParamKey,
  statusTone,
  toReportView,
  type ReflectionCandidate,
  type ReflectionChange,
  type ReflectionReportItem,
} from '../lib/reflection'
import type { Principal } from '../lib/auth'

const { Header, Content } = Layout

type ReflectionPageProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
  onOpenEditor: (graphId: string) => void
  onOpenMonitoring: () => void
}

/**
 * 反思进化 L2 v2（打包 ZS，docs/92）：反思报告的只读呈现 + 人工采纳入口（跳转引导）。
 *
 * **页内零写调用（U1108）**：所有数据只经 lib/reflection.ts 的只读面获取；
 * 本文件不 import 任何写函数，不做任何 POST/PUT/DELETE。「去修改」只负责跳转，
 * 修改动作由人工在对应管理面走既有流程（docs/88 P-1(a) 候选+人工确认，守 T22）。
 */
export function Reflection({
  principal,
  onLogout,
  onBack,
  onOpenEditor,
  onOpenMonitoring,
}: ReflectionPageProps) {
  const { t } = useTranslation('reflection')
  const { message } = App.useApp()
  const [reports, setReports] = useState<ReflectionReportItem[]>([])
  const [graphOptions, setGraphOptions] = useState<{ id: string }[]>([])
  const [graphFilter, setGraphFilter] = useState<string>('')
  const [candidates, setCandidates] = useState<Record<string, ReflectionCandidate>>({})
  const [loadingCandidateId, setLoadingCandidateId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const refresh = useCallback(
    async (graphId?: string) => {
      setLoading(true)
      setLoadError(null)
      try {
        const [items, graphs] = await Promise.all([loadReports(graphId), loadGraphOptions()])
        setReports(items)
        setGraphOptions(graphs)
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : String(error))
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    refresh()
  }, [refresh])
  /* oxlint-enable react/set-state-in-effect */

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh(graphFilter || undefined)
  }, [graphFilter, refresh])
  /* oxlint-enable react/set-state-in-effect */

  async function openCandidate(candidateId: string | null) {
    if (!candidateId || candidates[candidateId] || loadingCandidateId) return
    setLoadingCandidateId(candidateId)
    try {
      const detail = await loadCandidate(candidateId)
      setCandidates((prev) => ({ ...prev, [candidateId]: detail }))
    } catch {
      message.error(t('loadFailed'))
    } finally {
      setLoadingCandidateId(null)
    }
  }

  function gotoChange(change: ReflectionChange, graphId: string) {
    const scope = scopeForParamKey(change.param_key)
    const target = gotoTargetFor(scope, graphId)
    if (target.kind === 'editor') onOpenEditor(target.graphId)
    else if (target.kind === 'monitoring') onOpenMonitoring()
    // disabled：按钮本身已禁用，这里不做事
  }

  const changeColumns: ColumnsType<ReflectionChange> = [
    { title: t('candidate.param'), dataIndex: 'param_key', width: 220 },
    {
      title: t('candidate.from'),
      dataIndex: 'from',
      width: 140,
      render: (value: unknown) => (value === null || value === undefined ? '—' : String(value)),
    },
    {
      title: t('candidate.to'),
      dataIndex: 'to',
      width: 140,
      render: (value: unknown) => String(value),
    },
    { title: t('candidate.reason'), dataIndex: 'reason' },
    // actions 列在候选面板内按 report.graphId 渲染（gotoChange 需要图级上下文）
  ]

  const viewItems = filterReportsByGraph(reports, graphFilter || null).map(toReportView)

  const collapseItems = viewItems.map((report) => {
    const label = (
      <Space size="middle" wrap>
        <Typography.Text strong>{report.graphId}</Typography.Text>
        <Tag>v{report.baseVersion}</Tag>
        <Tag color={statusTone(report.status)}>{t(`status.${report.status}`)}</Tag>
        <Typography.Text type="secondary">{report.generatedAt}</Typography.Text>
        {report.hasCandidate ? (
          <Tag color="blue">{t('candidate.changes')}</Tag>
        ) : (
          <Tag>{t('candidate.none')}</Tag>
        )}
      </Space>
    )

    let children: React.ReactNode
    if (!report.hasCandidate) {
      children = (
        <Typography.Paragraph type="secondary">
          {report.reasons.length > 0 ? report.reasons.join('；') : t('candidate.none')}
        </Typography.Paragraph>
      )
    } else {
      const detail = report.candidateId ? candidates[report.candidateId] : undefined
      children = detail ? (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Table<ReflectionChange>
            rowKey="param_key"
            size="small"
            dataSource={detail.changes}
            columns={changeColumns.map((col) => ({
              ...col,
              render:
                col.key === 'actions'
                  ? (_, change) => {
                      const scope = scopeForParamKey(change.param_key)
                      const target = gotoTargetFor(scope, report.graphId)
                      return (
                        <Button
                          size="small"
                          disabled={target.kind === 'disabled'}
                          title={target.kind === 'disabled' ? t('goto.disabled') : undefined}
                          onClick={() => gotoChange(change, report.graphId)}
                        >
                          {t('candidate.gotoEdit')}
                        </Button>
                      )
                    }
                  : col.render,
            }))}
            locale={{ emptyText: t('candidate.none') }}
          />
          {detail.prompt_suggestions.length > 0 && (
            <Card size="small" title={t('candidate.suggestions')}>
              {detail.prompt_suggestions.map((suggestion, index) => (
                <Typography.Paragraph
                  key={`${index}-${suggestion.slice(0, 16)}`}
                  style={{ marginBottom: 8 }}
                >
                  <Typography.Text type="secondary" style={{ whiteSpace: 'pre-wrap' }}>
                    {suggestion}
                  </Typography.Text>
                </Typography.Paragraph>
              ))}
            </Card>
          )}
          <Typography.Text type="secondary">
            {t('candidate.digest')}：{detail.evidence_digest}
          </Typography.Text>
        </Space>
      ) : (
        <Button
          size="small"
          loading={loadingCandidateId === report.candidateId}
          onClick={() => openCandidate(report.candidateId)}
        >
          {t('candidate.changes')}
        </Button>
      )
      children = (
        <div
          role="button"
          tabIndex={0}
          style={{ cursor: 'default' }}
          onClick={(event) => {
            event.stopPropagation()
            void openCandidate(report.candidateId)
          }}
        >
          {children}
        </div>
      )
    }

    return { key: report.candidateId ?? report.generatedAt, label, children }
  })

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={onBack}>←</Button>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('title')}
          </Typography.Title>
        </Space>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Card
          loading={loading}
          extra={
            <Space>
              <Select
                size="small"
                style={{ minWidth: 160 }}
                value={graphFilter}
                onChange={setGraphFilter}
                options={[
                  { value: '', label: t('filter.allGraphs') },
                  ...graphOptions.map((graph) => ({ value: graph.id, label: graph.id })),
                ]}
              />
              <Button size="small" onClick={() => refresh(graphFilter || undefined)}>
                {t('refresh')}
              </Button>
            </Space>
          }
        >
          {loadError ? (
            <Space direction="vertical">
              <Typography.Text type="danger">{t('loadFailed')}</Typography.Text>
              <Typography.Text type="secondary">{loadError}</Typography.Text>
              <Typography.Text type="secondary">{t('loadFailedHint')}</Typography.Text>
            </Space>
          ) : isEmptyReports(viewItems) ? (
            <Empty
              description={
                <Space direction="vertical" size={4}>
                  <Typography.Text>{t('empty.title')}</Typography.Text>
                  <Typography.Text type="secondary">{t('empty.hint')}</Typography.Text>
                </Space>
              }
            />
          ) : (
            <Collapse items={collapseItems} />
          )}
        </Card>
      </Content>
    </Layout>
  )
}
