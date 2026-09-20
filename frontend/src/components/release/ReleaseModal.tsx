import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Collapse, Modal, Progress, Space, Spin, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  GateBlockedError,
  exportReleaseReport,
  getReleaseReport,
  listReleaseReports,
  getSubgraphUpgrades,
  publishGraph,
  runReleaseGate,
  type GateCaseRow,
  type GateReport,
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

  const loadUpgrades = useCallback(async () => {
    if (!graphId) {
      setUpgrades(null)
      return
    }
    setUpgrades(null)
    try {
      setUpgrades(await getSubgraphUpgrades(graphId))
    } catch {
      setUpgrades([])
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
    setLoading(true)
    setError(null)
    setReport(null)
    setPublishedVersion(null)
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
    if (open) {
      void loadGate()
      void loadUpgrades()
    }
  }, [open, loadGate, loadUpgrades])

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
      title: '结果',
      dataIndex: 'matches',
      width: 72,
      render: (matches: boolean) =>
        matches ? <Tag color="green">✓ 一致</Tag> : <Tag color="red">✗ 不匹配</Tag>,
    },
    { title: '用例', dataIndex: 'name' },
    { title: '回放终态', dataIndex: 'replay_status', width: 110 },
    { title: '说明', dataIndex: 'note' },
  ]

  const upgradeColumns: ColumnsType<SubgraphUpgrade> = [
    { title: '节点', dataIndex: 'node_id', width: 150 },
    { title: '子图', dataIndex: 'sub_id', width: 170 },
    {
      title: '版本变化',
      render: (_, row) =>
        row.first_pin || row.from_version === null ? (
          <Tag color="blue">首次钉版 @{row.to_version}</Tag>
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
      title: '时间',
      dataIndex: 'created_at',
      width: 110,
      render: (createdAt: string) => reportTimeLabel(createdAt),
    },
    {
      title: '触发方式',
      dataIndex: 'trigger',
      width: 100,
      render: (trigger: ReleaseReportSummary['trigger']) => {
        const meta = REPORT_TRIGGER_META[trigger]
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '通过/总数',
      width: 90,
      render: (_, row) => `${row.passed}/${row.total}`,
    },
    {
      title: '通过率趋势',
      dataIndex: 'pass_rate',
      width: 170,
      render: (passRate: number | null, row) =>
        passRate === null ? (
          <Tag>未覆盖</Tag>
        ) : (
          <Progress
            percent={Math.round(passRate * 100)}
            size="small"
            status={row.blocked ? 'exception' : 'success'}
          />
        ),
    },
    {
      title: '结论',
      width: 90,
      render: (_, row) => {
        const meta = GATE_CONCLUSION_META[reportConclusion(row)]
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '导出',
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
      title={`发布门禁${graphId ? `：${graphId}` : ''}`}
      open={open}
      width={760}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          {conclusion !== 'blocked' && publishedVersion === null && (
            <Button type="primary" loading={publishing} onClick={doPublish}>
              确认发布为新版本
            </Button>
          )}
        </Space>
      }
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        <Text type="secondary">
          对当前草稿批量回放本图录制用例并逐节点比对；存在不匹配用例时拦截发布（不产新版本）。
        </Text>
        {error && <Alert type="error" showIcon message={error} />}
        {publishedVersion !== null && (
          <Alert
            type="success"
            showIcon
            message={`已发布为 v${publishedVersion}（不可变版本）`}
          />
        )}
        {report && conclusion === 'blocked' && (
          <Alert
            type="error"
            showIcon
            message={`门禁未通过：${report.failed}/${report.total} 个用例不匹配，已禁止发布`}
          />
        )}
        {report && conclusion === 'skipped' && (
          <Alert
            type="warning"
            showIcon
            message="本图没有可回放的录制用例（未覆盖），门禁不阻塞发布；建议先录制黄金用例。"
          />
        )}
        {report && conclusion === 'passed' && (
          <Alert
            type="success"
            showIcon
            message={`门禁通过：${report.passed}/${report.total} 个用例全部一致，可发布。`}
          />
        )}
        {upgrades !== null && (
          <div>
            <Space style={{ justifyContent: 'space-between', width: '100%' }}>
              <Text strong>子图版本升级体检</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                发布将把子图引用钉到对应版本；升级后建议先跑门禁回归再发布（只读，不检测子图草稿改动）
              </Text>
            </Space>
            {upgrades.length === 0 ? (
              <Text type="secondary">本次发布无子图版本变化</Text>
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
          locale={{ emptyText: loading ? '回放中…' : '暂无匹配用例' }}
        />
        <Collapse
          ghost
          items={[
            {
              key: 'history',
              label: `历史报告（通过率趋势）${history.length ? `· ${history.length} 条` : ''}`,
              children: (
                <Table
                  rowKey="id"
                  size="small"
                  columns={historyColumns}
                  dataSource={history}
                  pagination={false}
                  locale={{ emptyText: '暂无历史报告（每次门禁/发布都会在此沉淀）' }}
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
