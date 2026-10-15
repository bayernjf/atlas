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
  abortAutoapplyRun,
  decisionLabelKey,
  decisionTone,
  filterReportsByGraph,
  gotoTargetFor,
  isEmptyReports,
  listAutoapplyRuns,
  loadCandidate,
  loadGraphOptions,
  loadReports,
  markDecision,
  scopeForParamKey,
  startCandidateAutoapply,
  statusTone,
  toReportView,
  type AutoApplyRun,
  type ReflectionCandidate,
  type ReflectionChange,
  type ReflectionDecisionStatus,
  type ReflectionReportItem,
} from '../lib/reflection'
import { roleCan } from '../lib/auth'
import type { Principal } from '../lib/auth'

const { Header, Content } = Layout

type ReflectionPageProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
  /** 打包 ZU（docs/94 E-6）：node_config 建议可携带节点 id，打开编辑器后选中并居中。 */
  onOpenEditor: (graphId: string, nodeId?: string | null) => void
  onOpenMonitoring: () => void
}

/**
 * 反思进化 L2（打包 ZS docs/92；打包 ZU docs/94 增候选决策标记与节点级定位；
 * 打包 BP docs/132 增候选自动生效与每圈证据记录）：
 * 反思报告呈现 + 人工采纳入口（跳转引导 + 候选级处理标记）+ 候选自动生效。
 *
 * **写调用面（U1124 机检）**：本文件不直接 import apiClient、无任何 method 字面；
 * 读经 lib/reflection.ts 只读面；写动作经 lib 的 `markDecision`（=putReflectionDecision，
 * 标记只记录人的处理决定，不改图、不发布、不碰路由，守 T22）与打包 BP 新增的
 * `startCandidateAutoapply`／`abortAutoapplyRun`（自动生效推进/中止，端点在候选与
 * run 上，publish/canary 均由后端门控推进）。真正改图仍由人点「去修改」跳转后在
 * 编辑器走既有写端点。
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
  const canOperate = roleCan(principal.role, 'operate')
  const [reports, setReports] = useState<ReflectionReportItem[]>([])
  const [graphOptions, setGraphOptions] = useState<{ id: string }[]>([])
  const [graphFilter, setGraphFilter] = useState<string>('')
  const [candidates, setCandidates] = useState<Record<string, ReflectionCandidate>>({})
  const [loadingCandidateId, setLoadingCandidateId] = useState<string | null>(null)
  const [decidingId, setDecidingId] = useState<string | null>(null)
  // 打包 BP（docs/132 D75）：自动生效记录按候选存（打开候选时加载），进行中/中止态。
  const [autoapplyByCandidate, setAutoapplyByCandidate] = useState<
    Record<string, AutoApplyRun[]>
  >({})
  const [autoapplyingId, setAutoapplyingId] = useState<string | null>(null)
  const [abortingId, setAbortingId] = useState<string | null>(null)
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
      // 打包 BP：打开候选时顺带加载其自动生效记录（倒序，取本候选的行）。
      const runs = (await listAutoapplyRuns(detail.graph_id)).filter(
        (run) => run.candidate_id === candidateId,
      )
      setAutoapplyByCandidate((prev) => ({ ...prev, [candidateId]: runs }))
    } catch {
      message.error(t('loadFailed'))
    } finally {
      setLoadingCandidateId(null)
    }
  }

  // 打包 BP（docs/132 BP-D2/D4）：候选自动生效（一鼓作气 patch→影子→门禁→发布→canary）。
  async function autoapply(candidateId: string) {
    if (autoapplyingId) return
    setAutoapplyingId(candidateId)
    try {
      const run = await startCandidateAutoapply(candidateId)
      setAutoapplyByCandidate((prev) => ({
        ...prev,
        [candidateId]: [run, ...(prev[candidateId] ?? [])],
      }))
      message.success(
        run.status === 'completed'
          ? t('autoapply.startedOk')
          : t('autoapply.startedFail', { reason: run.reason ?? '' }),
      )
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      // 409（幂等/已处理候选）与 404 直接提示，不刷新。
      message.warning(detail)
    } finally {
      setAutoapplyingId(null)
    }
  }

  // 打包 BP（docs/132 BP-D5）：人可随时中止；canary 态＝手动回滚。
  async function abortRun(candidateId: string, runId: string) {
    if (abortingId) return
    setAbortingId(runId)
    try {
      const run = await abortAutoapplyRun(runId)
      setAutoapplyByCandidate((prev) => ({
        ...prev,
        [candidateId]: (prev[candidateId] ?? []).map((row) =>
          row.run_id === runId ? run : row,
        ),
      }))
      message.success(t('autoapply.abortedOk'))
    } catch (error) {
      message.warning(error instanceof Error ? error.message : String(error))
    } finally {
      setAbortingId(null)
    }
  }

  function gotoChange(change: ReflectionChange, graphId: string) {
    const scope = scopeForParamKey(change.param_key)
    // 打包 ZU（docs/94 E-6）：node_config 建议携带节点 id，打开编辑器后选中并居中。
    const nodeId = scope === 'node_config' ? change.node_id ?? null : null
    const target = gotoTargetFor(scope, graphId, nodeId)
    if (target.kind === 'editor') onOpenEditor(target.graphId, target.nodeId ?? undefined)
    else if (target.kind === 'monitoring') onOpenMonitoring()
    // disabled：按钮本身已禁用，这里不做事
  }

  // 打包 ZU（docs/94 E-1/E-4）：登记/改判候选处理标记；只记标记，不改图、不发布（守 T22）。
  async function decide(candidateId: string, status: ReflectionDecisionStatus) {
    if (decidingId) return
    setDecidingId(candidateId)
    try {
      const updated = await markDecision(candidateId, status)
      setCandidates((prev) => ({ ...prev, [candidateId]: updated }))
      setReports((prev) =>
        prev.map((row) =>
          row.candidate_id === candidateId
            ? { ...row, decision_status: updated.decision_status ?? null }
            : row,
        ),
      )
      message.success(t(decisionLabelKey(updated.decision_status)))
    } catch {
      message.error(t('decision.failed'))
    } finally {
      setDecidingId(null)
    }
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
    // 打包 ZU（docs/94 E-7/U1124）：actions 列必须真实存在（ZS 曾只有 map 替换、列定义缺失，
    // 致「去修改」从未渲染）；render 在下方按 report.graphId/change.node_id 上下文注入。
    { key: 'actions', title: t('candidate.actions'), width: 120, render: () => null },
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
        {report.hasCandidate && report.decisionStatus && (
          <Tag color={decisionTone(report.decisionStatus)}>
            {t(decisionLabelKey(report.decisionStatus))}
          </Tag>
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
                      const nodeId = scope === 'node_config' ? change.node_id ?? null : null
                      const target = gotoTargetFor(scope, report.graphId, nodeId)
                      const titleTip =
                        target.kind === 'disabled'
                          ? t('goto.disabled')
                          : target.kind === 'editor' && target.nodeId
                            ? t('decision.gotoNode', { nodeId: target.nodeId })
                            : undefined
                      return (
                        <Button
                          size="small"
                          disabled={target.kind === 'disabled'}
                          title={titleTip}
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
          <Space size="middle" wrap>
            <Tag color={decisionTone(detail.decision_status)}>
              {t(decisionLabelKey(detail.decision_status))}
            </Tag>
            {canOperate && (
              <>
                <Button
                  size="small"
                  type={detail.decision_status === 'adopted' ? 'primary' : 'default'}
                  loading={decidingId === detail.candidate_id}
                  onClick={() => decide(detail.candidate_id, 'adopted')}
                >
                  {t('decision.markAdopted')}
                </Button>
                <Button
                  size="small"
                  loading={decidingId === detail.candidate_id}
                  onClick={() => decide(detail.candidate_id, 'dismissed')}
                >
                  {t('decision.markDismissed')}
                </Button>
                {/* 打包 BP（docs/132 D75）：候选自动生效——仅未处理候选可用（已处理 409）。 */}
                <Button
                  size="small"
                  type="primary"
                  ghost
                  disabled={detail.decision_status != null}
                  loading={autoapplyingId === detail.candidate_id}
                  onClick={() => autoapply(detail.candidate_id)}
                >
                  {t('autoapply.start')}
                </Button>
              </>
            )}
          </Space>
          {(autoapplyByCandidate[detail.candidate_id] ?? []).length > 0 && (
            <Card
              size="small"
              title={t('autoapply.title')}
              styles={{ body: { padding: 8 } }}
            >
              <Table<AutoApplyRun>
                rowKey="run_id"
                size="small"
                pagination={false}
                dataSource={autoapplyByCandidate[detail.candidate_id]}
                columns={[
                  { title: t('autoapply.runId'), dataIndex: 'run_id', width: 110 },
                  {
                    title: t('autoapply.status'),
                    dataIndex: 'status',
                    width: 120,
                    render: (value: AutoApplyRun['status']) => (
                      <Tag color={value === 'completed' ? 'success' : value === 'failed' ? 'error' : 'processing'}>
                        {t(`autoapply.status.${value}`)}
                      </Tag>
                    ),
                  },
                  {
                    title: t('autoapply.publish'),
                    dataIndex: 'publish_version',
                    width: 90,
                    render: (value: number | null) =>
                      value == null ? '—' : `v${value}`,
                  },
                  {
                    title: t('autoapply.rollout'),
                    dataIndex: 'rollout_status',
                    width: 110,
                    render: (value: string | null) => (value ? String(value) : '—'),
                  },
                  {
                    title: t('autoapply.reason'),
                    dataIndex: 'reason',
                    render: (value: string | null) =>
                      value ? (
                        <Typography.Text type="secondary" style={{ whiteSpace: 'pre-wrap' }}>
                          {value}
                        </Typography.Text>
                      ) : (
                        '—'
                      ),
                  },
                  {
                    title: t('autoapply.stages'),
                    dataIndex: 'stages',
                    render: (stages: AutoApplyRun['stages']) => (
                      <Space size={2} wrap>
                        {stages.map((stage) => (
                          <Tag
                            key={`${stage.stage}-${stage.at}`}
                            color={stage.ok ? 'default' : 'error'}
                            style={{ fontSize: 11 }}
                          >
                            {stage.stage}
                            {stage.ok ? '' : '✗'}
                          </Tag>
                        ))}
                      </Space>
                    ),
                  },
                  {
                    key: 'abort',
                    title: '',
                    width: 70,
                    render: (_, run) =>
                      run.status === 'completed' && run.rollout_status === 'canary' ? (
                        <Button
                          size="small"
                          danger
                          loading={abortingId === run.run_id}
                          onClick={() => abortRun(detail.candidate_id, run.run_id)}
                        >
                          {t('autoapply.abort')}
                        </Button>
                      ) : null,
                  },
                ]}
              />
            </Card>
          )}
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
