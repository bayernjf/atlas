import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Collapse, Modal, Progress, Space, Spin, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  GateBlockedError,
  exportReleaseReport,
  getReleaseReport,
  listReleaseReports,
  getGraphDiff,
  getSubgraphUpgrades,
  publishGraph,
  runReleaseGate,
  type GateCaseRow,
  type GateReport,
  type GraphDiffResponse,
  type ReleaseReport,
  type ReleaseReportSummary,
  type SubgraphUpgrade,
} from '../../lib/apiClient'
import {
  GATE_CONCLUSION_META,
  REPORT_TRIGGER_META,
  gateConclusion,
  reportConclusion,
  reportTimeLabel,
} from '../../lib/release'
import { useTranslation } from '../../locales'

const { Text } = Typography

type Props = {
  open: boolean
  graphId: string | null
  onClose: () => void
  /** 发布成功（产新版本）后回调，供父级刷新版本列表 */
  onPublished: (releaseVersion: number) => void
}

/**
 * 发布流门禁 Modal（M9，U59 ②；03 `release_gate`、04 §5.11 末）：
 * 打开即对当前 latest 草稿批量回放 graph_id 匹配的录制用例，逐例 ✓/✗ + note；
 * blocked 禁止发布、total=0 skipped 明示未覆盖但不阻塞。
 */
export function ReleaseModal({ open, graphId, onClose, onPublished }: Props) {
  const { t } = useTranslation('editor')
  const [loading, setLoading] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [report, setReport] = useState<GateReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null)
  // D26 报告 v1：历史报告（倒序摘要）+ 展开行懒加载详情
  const [history, setHistory] = useState<ReleaseReportSummary[]>([])
  const [detailById, setDetailById] = useState<Record<string, ReleaseReport>>({})
  // ⑪ 子图版本升级体检（只读，与门禁并行加载；失败 fail-safe 不阻断）
  const [upgrades, setUpgrades] = useState<SubgraphUpgrade[] | null>(null)
  // B3 与上一版本的配置结构差异（只读，与门禁并行加载；失败 fail-safe 不阻断）
  const [graphDiff, setGraphDiff] = useState<GraphDiffResponse | null>(null)

  // 打开弹窗时重置上一轮状态（渲染期按 prop 变化重置，避免 effect 内同步 setState）
  const [prevOpen, setPrevOpen] = useState(open)
  if (open !== prevOpen) {
    setPrevOpen(open)
    if (open) {
      setLoading(true)
      setError(null)
      setReport(null)
      setPublishedVersion(null)
      setUpgrades(null)
      setGraphDiff(null)
    }
  }

  const loadUpgrades = useCallback(async () => {
    if (!graphId) return
    // 重置由打开弹窗时的渲染期逻辑负责
    try {
      setUpgrades(await getSubgraphUpgrades(graphId))
    } catch {
      setUpgrades([])
    }
  }, [graphId])

  const loadDiff = useCallback(async () => {
    if (!graphId) return
    try {
      setGraphDiff(await getGraphDiff(graphId))
    } catch {
      setGraphDiff(null)
    }
  }, [graphId])

  const loadHistory = useCallback(async () => {
    if (!graphId) return
    try {
      setHistory(await listReleaseReports(graphId))
    } catch {
      // 历史是辅助视图，加载失败不阻断门禁主流程
      setHistory([])
    }
  }, [graphId])

  const loadGate = useCallback(async () => {
    if (!graphId) return
    // 加载态/重置由打开弹窗时的渲染期逻辑负责，此处只做异步获取
    try {
      setReport(await runReleaseGate(graphId))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setLoading(false)
    }
    void loadHistory()  // 手动门禁沉淀后刷新历史（含本次）
  }, [graphId, loadHistory])

  useEffect(() => {
    // 数据获取 effect（fetch-on-mount）：两个 loader 的 setState 均在 await 之后，无同步级联渲染
    if (open) {
      // oxlint-disable-next-line react/set-state-in-effect
      void loadGate()
      // oxlint-disable-next-line react/set-state-in-effect
      void loadUpgrades()
      // oxlint-disable-next-line react/set-state-in-effect
      void loadDiff()
    }
  }, [open, loadGate, loadUpgrades, loadDiff])

  const doPublish = async () => {
    if (!graphId) return
    setPublishing(true)
    setError(null)
    try {
      // gate:true 以后端为权威再跑一次门禁；blocked → 409 GateBlockedError 不产版本
      const result = await publishGraph(graphId, true)
      setPublishedVersion(result.releaseVersion)
      onPublished(result.releaseVersion)
    } catch (publishError) {
      if (publishError instanceof GateBlockedError) {
        setReport(publishError.report)
      } else {
        setError(publishError instanceof Error ? publishError.message : String(publishError))
      }
    } finally {
      setPublishing(false)
      void loadHistory()  // 发布门禁（通过/blocked/skipped）均沉淀，刷新历史
    }
  }

  const conclusion = report ? gateConclusion(report) : null

  const columns: ColumnsType<GateCaseRow> = [
    {
      title: t('release.col.result'),
      dataIndex: 'matches',
      width: 72,
      render: (matches: boolean) =>
        matches ? <Tag color="green">{t('release.match')}</Tag> : <Tag color="red">{t('release.mismatch')}</Tag>,
    },
    { title: t('release.col.case'), dataIndex: 'name' },
    { title: t('release.col.replayStatus'), dataIndex: 'replay_status', width: 110 },
    { title: t('release.col.note'), dataIndex: 'note' },
  ]

  const upgradeColumns: ColumnsType<SubgraphUpgrade> = [
    { title: t('release.upgrade.col.node'), dataIndex: 'node_id', width: 150 },
    { title: t('release.upgrade.col.subgraph'), dataIndex: 'sub_id', width: 170 },
    {
      title: t('release.upgrade.col.change'),
      render: (_, row) =>
        row.first_pin || row.from_version === null ? (
          <Tag color="blue">{t('release.upgrade.firstPin', { version: row.to_version })}</Tag>
        ) : (
          <Space size={4}>
            <Tag>@{row.from_version}</Tag>
            <span>→</span>
            <Tag color={row.to_version > (row.from_version ?? 0) ? 'orange' : 'default'}>
              @{row.to_version}
            </Tag>
          </Space>
        ),
    },
  ]

  const doExport = async (row: ReleaseReportSummary, format: 'csv' | 'json') => {
    if (!graphId) return
    try {
      await exportReleaseReport(graphId, row.id, format)
    } catch {
      // 下载为辅助动作，失败不打断门禁主流程
    }
  }

  const onExpandHistory = async (expanded: boolean, row: ReleaseReportSummary) => {
    if (!expanded || !graphId || detailById[row.id]) return
    try {
      const detail = await getReleaseReport(graphId, row.id)
      setDetailById((prev) => ({ ...prev, [row.id]: detail }))
    } catch {
      // 单行详情失败不影响列表，展开区保持空
    }
  }

  const historyColumns: ColumnsType<ReleaseReportSummary> = [
    {
      title: t('release.col.time'),
      dataIndex: 'created_at',
      width: 110,
      render: (createdAt: string) => reportTimeLabel(createdAt),
    },
    {
      title: t('release.col.trigger'),
      dataIndex: 'trigger',
      width: 100,
      render: (trigger: ReleaseReportSummary['trigger']) => {
        const meta = REPORT_TRIGGER_META[trigger]
        return <Tag color={meta.color}>{t(meta.label)}</Tag>
      },
    },
    {
      title: t('release.col.passTotal'),
      width: 90,
      render: (_, row) => `${row.passed}/${row.total}`,
    },
    {
      title: t('release.col.trend'),
      dataIndex: 'pass_rate',
      width: 170,
      render: (passRate: number | null, row) =>
        passRate === null ? (
          <Tag>{t('release.uncovered')}</Tag>
        ) : (
          <Progress
            percent={Math.round(passRate * 100)}
            size="small"
            status={row.blocked ? 'exception' : 'success'}
          />
        ),
    },
    {
      title: t('release.col.conclusion'),
      width: 90,
      render: (_, row) => {
        const meta = GATE_CONCLUSION_META[reportConclusion(row)]
        return <Tag color={meta.color}>{t(meta.label)}</Tag>
      },
    },
    {
      title: t('release.col.export'),
      width: 104,
      render: (_, row) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            style={{ padding: 0 }}
            onClick={(event) => {
              event.stopPropagation()
              void doExport(row, 'csv')
            }}
          >
            CSV
          </Button>
          <Button
            type="link"
            size="small"
            style={{ padding: 0 }}
            onClick={(event) => {
              event.stopPropagation()
              void doExport(row, 'json')
            }}
          >
            JSON
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Modal
      title={graphId ? t('release.titleWithGraph', { graphId }) : t('release.title')}
      open={open}
      width={760}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>{t('common:button.close')}</Button>
          {conclusion !== 'blocked' && publishedVersion === null && (
            <Button type="primary" loading={publishing} onClick={doPublish}>
              {t('release.confirmPublish')}
            </Button>
          )}
        </Space>
      }
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        <Text type="secondary">{t('release.intro')}</Text>
        {error && <Alert type="error" showIcon message={error} />}
        {publishedVersion !== null && (
          <Alert
            type="success"
            showIcon
            message={t('release.published', { version: publishedVersion })}
          />
        )}
        {report && conclusion === 'blocked' && (
          <Alert
            type="error"
            showIcon
            message={t('release.blocked', { failed: report.failed, total: report.total })}
          />
        )}
        {report && conclusion === 'skipped' && (
          <Alert type="warning" showIcon message={t('release.skipped')} />
        )}
        {report && conclusion === 'passed' && (
          <Alert
            type="success"
            showIcon
            message={t('release.passed', { passed: report.passed, total: report.total })}
          />
        )}
        {upgrades !== null && (
          <div>
            <Space style={{ justifyContent: 'space-between', width: '100%' }}>
              <Text strong>{t('release.upgrade.title')}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('release.upgrade.hint')}
              </Text>
            </Space>
            {upgrades.length === 0 ? (
              <Text type="secondary">{t('release.upgrade.empty')}</Text>
            ) : (
              <Table
                rowKey="node_id"
                size="small"
                pagination={false}
                dataSource={upgrades}
                columns={upgradeColumns}
                style={{ marginTop: 8 }}
              />
            )}
          </div>
        )}
        <Table
          rowKey="case_id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={report?.cases ?? []}
          pagination={false}
          locale={{ emptyText: loading ? t('release.replaying') : t('release.emptyCases') }}
        />
        {graphDiff && (
          <Collapse
            ghost
            items={[
              {
                key: 'configDiff',
                label: t('release.diff.label', {
                  base:
                    graphDiff.fromVersion === null
                      ? t('release.diff.emptyGraph')
                      : `v${graphDiff.fromVersion}`,
                  summary:
                    diffLabelParts(graphDiff.summary, t).join(' · ') || t('release.diff.noChange'),
                }),
                children: <DiffView data={graphDiff.diff} />,
              },
            ]}
          />
        )}
        <Collapse
          ghost
          items={[
            {
              key: 'history',
              label: history.length
                ? t('release.diff.historyTitleWithCount', { count: history.length })
                : t('release.diff.historyTitle'),
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  columns={historyColumns}
                  dataSource={history}
                  pagination={false}
                  locale={{ emptyText: t('release.diff.emptyHistory') }}
                  expandable={{
                    onExpand: onExpandHistory,
                    expandedRowRender: (row) => {
                      const detail = detailById[row.id]
                      if (!detail) {
                        return <Spin size="small" />
                      }
                      return (
                        <Table
                          rowKey="case_id"
                          size="small"
                          columns={columns}
                          dataSource={detail.cases}
                          pagination={false}
                        />
                      )
                    },
                  }}
                />
              ),
            },
          ]}
        />
      </Space>
    </Modal>
  )
}

type Translate = (key: string, vars?: Record<string, unknown>) => string

/** B3：把 diff summary 折叠为「节点 +N」等本地化片段（无变化返回空数组）。 */
function diffLabelParts(summary: Record<string, number>, t: Translate): string[] {
  return [
    summary.nodesAdded && t('release.diff.part.nodesAdded', { count: summary.nodesAdded }),
    summary.nodesRemoved && t('release.diff.part.nodesRemoved', { count: summary.nodesRemoved }),
    summary.nodesChanged && t('release.diff.part.nodesChanged', { count: summary.nodesChanged }),
    summary.edgesAdded && t('release.diff.part.edgesAdded', { count: summary.edgesAdded }),
    summary.edgesRemoved && t('release.diff.part.edgesRemoved', { count: summary.edgesRemoved }),
    summary.variablesAdded && t('release.diff.part.variablesAdded', { count: summary.variablesAdded }),
    summary.variablesRemoved && t('release.diff.part.variablesRemoved', { count: summary.variablesRemoved }),
    summary.variablesChanged && t('release.diff.part.variablesChanged', { count: summary.variablesChanged }),
  ].filter((x): x is string => Boolean(x))
}

/** B3：渲染两版 graph 的配置结构差异。 */
function DiffView({ data }: { data: GraphDiffResponse['diff'] }) {
  const { t, i18n } = useTranslation('editor')
  const { nodes, edges, variables } = data
  const listSeparator = i18n.language === 'en-US' ? ', ' : '、'
  const empty =
    nodes.added.length === 0 && nodes.removed.length === 0 && nodes.changed.length === 0 &&
    edges.added.length === 0 && edges.removed.length === 0 &&
    variables.added.length === 0 && variables.removed.length === 0 && variables.changed.length === 0
  if (empty) return <Text type="secondary">{t('release.diff.empty')}</Text>
  return (
    <Space orientation="vertical" size={8} style={{ width: '100%' }}>
      {nodes.added.length > 0 && (
        <div><Text type="success">{t('release.diff.view.nodesAdded')}</Text>{nodes.added.map((id) => <Tag key={id} color="success">{id}</Tag>)}</div>
      )}
      {nodes.removed.length > 0 && (
        <div><Text type="danger">{t('release.diff.view.nodesRemoved')}</Text>{nodes.removed.map((id) => <Tag key={id} color="error">{id}</Tag>)}</div>
      )}
      {nodes.changed.length > 0 && (
        <div>
          <Text strong>{t('release.diff.view.nodesChanged')}</Text>
          {nodes.changed.map((c) => (
            <div key={c.id} style={{ marginLeft: 8, marginTop: 4 }}>
              <Tag>{c.id}</Tag>
              {c.changes.map((ch, i) =>
                ch.field === 'config' ? (
                  <Tag key={i} color="blue">{t('release.diff.view.configKeys', { keys: ch.configKeys.join(listSeparator) })}</Tag>
                ) : (
                  <Tag key={i} color="orange">{t('release.diff.view.fieldChange', { field: ch.field, from: String(ch.from), to: String(ch.to) })}</Tag>
                ),
              )}
            </div>
          ))}
        </div>
      )}
      {edges.added.length > 0 && (
        <div><Text type="success">{t('release.diff.view.edgesAdded')}</Text>{edges.added.map((id) => <Tag key={id} color="success">{id}</Tag>)}</div>
      )}
      {edges.removed.length > 0 && (
        <div><Text type="danger">{t('release.diff.view.edgesRemoved')}</Text>{edges.removed.map((id) => <Tag key={id} color="error">{id}</Tag>)}</div>
      )}
      {variables.added.length > 0 && (
        <div><Text type="success">{t('release.diff.view.variablesAdded')}</Text>{variables.added.map((n) => <Tag key={n} color="success">{n}</Tag>)}</div>
      )}
      {variables.removed.length > 0 && (
        <div><Text type="danger">{t('release.diff.view.variablesRemoved')}</Text>{variables.removed.map((n) => <Tag key={n} color="error">{n}</Tag>)}</div>
      )}
      {variables.changed.length > 0 && (
        <div><Text strong>{t('release.diff.view.variablesChanged')}</Text>{variables.changed.map((n) => <Tag key={n} color="blue">{n}</Tag>)}</div>
      )}
    </Space>
  )
}

