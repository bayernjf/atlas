import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Layout,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  acknowledgeAlert,
  getMetrics,
  getRules,
  getRuns,
  listAlerts,
  listAllReleaseReports,
  resolveAlert,
  updateRules,
  type AlertItem,
  type AlertStatus,
  type MetricsSummary,
  type ReleaseReportSummary,
  type RuleConfig,
  type RunRecord,
  type ToolMetricsRow,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import { ShadowRunsCard } from '../components/shadow/ShadowRunsCard'
import { TraceWaterfall } from '../components/monitoring/TraceWaterfall'
import { OnCallBar } from '../components/monitoring/OnCallBar'
import { WebhookReliabilityCard } from '../components/monitoring/WebhookReliabilityCard'
import { AlertChannelCard } from '../components/monitoring/AlertChannelCard'
import { SilenceManager, SilencePopButton } from '../components/monitoring/SilenceManager'
import { AlertRuleTemplateMarket } from '../components/monitoring/AlertRuleTemplateMarket'
import { RuleConfigEditor } from '../components/monitoring/RuleConfigEditor'
import { validateRuleConfig } from '../lib/ruleConfig'
import {
  ALERT_STATUS_COLORS,
  ALERT_STATUS_LABELS,
  formatDuration,
  formatTime,
  runHealth,
  ruleLabel,
  SEVERITY_COLORS,
} from '../lib/monitoring'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type MonitoringProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

const EMPTY_METRICS: MetricsSummary = {
  total: 0,
  healthy: 0,
  unhealthy: 0,
  success_rate: null,
  p50: null,
  p95: null,
  per_graph: [],
  failed_nodes: [],
  tools: [],
  business: {
    auto_refund_rate: null,
    manual_escalation_rate: null,
    refund_amount_diff_rate: null,
    per_graph: [],
    per_version: [],
  },
}

function rateText(rate: number | null): string {
  return rate === null ? '—' : `${(rate * 100).toFixed(1)}%`
}

export function Monitoring({ principal, onLogout, onBack }: MonitoringProps) {
  const { t } = useTranslation('monitoring')
  const canOperate = roleCan(principal.role, 'operate')
  const canAdmin = roleCan(principal.role, 'administer')
  const [metrics, setMetrics] = useState<MetricsSummary>(EMPTY_METRICS)
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [rules, setRules] = useState<RuleConfig | null>(null)
  const [crossReports, setCrossReports] = useState<ReleaseReportSummary[]>([])
  const [statusFilter, setStatusFilter] = useState<'all' | AlertStatus>('all')
  const [graphFilter, setGraphFilter] = useState<'all' | string>('all')
  const [loadError, setLoadError] = useState('')
  const [ruleError, setRuleError] = useState('')
  const [ruleSaved, setRuleSaved] = useState(false)
  const [silenceVersion, setSilenceVersion] = useState(0)

  const refresh = useCallback(async () => {
    try {
      const [metricsData, alertsData, runsData, crossReportsData] = await Promise.all([
        getMetrics(),
        listAlerts(),
        getRuns(graphFilter === 'all' ? undefined : graphFilter),
        listAllReleaseReports(100),
      ])
      setMetrics(metricsData)
      setAlerts(alertsData)
      setRuns(runsData)
      setCrossReports(crossReportsData)
      setLoadError('')
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }, [graphFilter])

  useEffect(() => {
    getRules().then(setRules).catch(() => undefined)
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [refresh])

  const visibleAlerts =
    statusFilter === 'all' ? alerts : alerts.filter((alert) => alert.status === statusFilter)
  const unresolvedCount = alerts.filter((alert) => alert.status !== 'resolved').length

  const mutateAlert = async (action: (id: string) => Promise<AlertItem>, id: string) => {
    try {
      await action(id)
      await refresh()
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  const saveRules = async () => {
    if (!rules) return
    setRuleError('')
    setRuleSaved(false)
    // docs/28 §4.2：保存前逐行前端校验（与后端 validate_rules 同构，禁 eval 引擎）
    // docs/103：校验逻辑收敛到共享 RuleConfigEditor.validateRuleConfig
    for (const error of validateRuleConfig(rules)) {
      const rule = rules.custom?.[error.index]
      if (error.kind === 'custom-name-empty') {
        setRuleError(t('rules.nameEmpty', { cid: rule?.cid ?? '' }))
        return
      }
      setRuleError(t('rules.exprInvalid', { name: rule?.name ?? '', errors: error.messages.join('；') }))
      return
    }
    try {
      const saved = await updateRules(rules)
      setRules(saved)
      setRuleSaved(true)
      await refresh()
    } catch (error) {
      setRuleError(error instanceof Error ? error.message : String(error))
    }
  }

  const alertColumns: ColumnsType<AlertItem> = [
    {
      title: t('col.rule'),
      dataIndex: 'rule_id',
      width: 110,
      render: (ruleId: AlertItem['rule_id'], alert) => (
        <Space orientation="vertical" size={0}>
          <span>{t(ruleLabel(ruleId, alert.rule_name))}</span>
          <Tag color={SEVERITY_COLORS[alert.severity]} style={{ marginTop: 2 }}>
            {alert.severity === 'critical' ? t('severity.critical') : t('severity.warning')}
          </Tag>
          {alert.escalated_at && (
            <Tooltip title={formatTime(alert.escalated_at)}>
              <Tag color="red" style={{ marginTop: 2 }}>
                {t('escalation.tag')}
              </Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    { title: t('col.graph'), dataIndex: 'graph_id', width: 140 },
    {
      title: t('col.assignee'),
      dataIndex: 'assignee',
      width: 100,
      render: (assignee: string | null | undefined) => assignee ?? '—',
    },
    {
      title: t('col.message'),
      key: 'message',
      render: (_, alert) => (
        <Space orientation="vertical" size={2}>
          <span>{alert.message}</span>
          {alert.action?.type === 'rollback' && (
            <Tag color="volcano" style={{ marginTop: 2 }}>
              {t('rollback.tag', {
                actor: alert.action.actor === 'auto' ? t('rollback.auto') : t('rollback.manual'),
                from: alert.action.from_version,
                to: alert.action.to_version,
              })}
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: t('col.count'),
      dataIndex: 'count',
      width: 70,
      sorter: (a, b) => a.count - b.count,
    },
    {
      title: t('col.lastRun'),
      dataIndex: 'last_run_id',
      width: 90,
    },
    {
      title: t('col.status'),
      dataIndex: 'status',
      width: 90,
      render: (status: AlertStatus) => (
        <Tag color={ALERT_STATUS_COLORS[status]}>{t(ALERT_STATUS_LABELS[status])}</Tag>
      ),
    },
    {
      title: t('col.lastSeen'),
      dataIndex: 'last_seen',
      width: 100,
      render: (value: string) => formatTime(value),
    },
    {
      title: t('col.actions'),
      key: 'actions',
      width: 230,
      render: (_, alert) =>
        canOperate ? (
          <Space>
            {canAdmin && (
              <SilencePopButton
                ruleId={alert.rule_id}
                graphId={alert.graph_id}
                onCreated={() => {
                  setSilenceVersion((v) => v + 1)
                  refresh()
                }}
              />
            )}
            <Button
              size="small"
              disabled={alert.status !== 'open'}
              onClick={() => mutateAlert(acknowledgeAlert, alert.id)}
            >
              {t('alert.acknowledge')}
            </Button>
            <Button
              size="small"
              type="primary"
              ghost
              disabled={alert.status === 'resolved'}
              onClick={() => mutateAlert(resolveAlert, alert.id)}
            >
              {t('alert.resolve')}
            </Button>
          </Space>
        ) : null,
    },
  ]

  const runColumns: ColumnsType<RunRecord> = [
    { title: t('col.run'), dataIndex: 'id', width: 90 },
    { title: t('col.graph'), dataIndex: 'graph_id', width: 130 },
    {
      title: t('col.version'),
      dataIndex: 'resolved_version',
      width: 80,
      render: (value: number | null) => (value === null ? t('version.draft') : `v${value}`),
    },
    {
      title: t('col.mode'),
      dataIndex: 'mode',
      width: 80,
      render: (mode: string) => (mode === 'sync' ? t('mode.sync') : t('mode.stream')),
    },
    {
      title: t('col.status'),
      key: 'health',
      width: 90,
      render: (_, record) => {
        const health = runHealth(record)
        if (health === 'error') return <Tag color="red">{t('health.error')}</Tag>
        if (health === 'unhealthy') return <Tag color="orange">{t('health.unhealthy')}</Tag>
        return <Tag color="green">{t('health.healthy')}</Tag>
      },
    },
    {
      title: t('col.nodes'),
      key: 'nodes',
      render: (_, record) => {
        const failed = record.nodes.filter((node) => node.status === 'failed').length
        return t('run.nodeSummary', { ok: record.nodes.length - failed, failed })
      },
    },
    {
      title: t('col.duration'),
      dataIndex: 'duration_ms',
      width: 100,
      render: (value: number) => formatDuration(value),
    },
    {
      title: t('col.started'),
      dataIndex: 'started_at',
      width: 110,
      render: (value: string) => formatTime(value),
    },
  ]

  const crossReportColumns: ColumnsType<ReleaseReportSummary> = [
    {
      title: t('col.time'),
      dataIndex: 'created_at',
      width: 180,
      render: (value) => formatTime(value as string),
    },
    { title: t('col.graphId'), dataIndex: 'graph_id', ellipsis: true },
    {
      title: t('col.trigger'),
      dataIndex: 'trigger',
      width: 100,
      render: (value) => (value === 'publish-gate' ? t('trigger.publishGate') : t('trigger.manualGate')),
    },
    {
      title: t('col.passRate'),
      dataIndex: 'pass_rate',
      width: 110,
      render: (value) => {
        const rate = value as number | null
        if (rate === null) return <Tag>{t('report.uncovered')}</Tag>
        const color = rate >= 1 ? 'green' : rate >= 0.8 ? 'orange' : 'red'
        return <Tag color={color}>{(rate * 100).toFixed(1)}%</Tag>
      },
    },
    {
      title: t('col.passTotal'),
      width: 100,
      render: (_, record) => `${record.passed}/${record.total}`,
    },
    {
      title: t('col.blocked'),
      dataIndex: 'blocked',
      width: 90,
      render: (value) =>
        value ? <Tag color="red">{t('blocked.yes')}</Tag> : <Tag color="green">{t('blocked.no')}</Tag>,
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
            <Badge count={unresolvedCount} title={t('header.unresolvedBadge')}>
              <Button onClick={refresh}>{t('header.refresh')}</Button>
            </Badge>
            <Button onClick={onBack}>{t('header.back')}</Button>
            <UserBadge principal={principal} onLogout={onLogout} />
          </Space>
        </Space>
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          {loadError && <Alert type="error" showIcon message={loadError} />}

          <Row gutter={16}>
            <Col span={4}>
              <Card>
                <Statistic title={t('metric.total')} value={metrics.total} />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic
                  title={t('metric.healthy')}
                  value={metrics.healthy}
                  styles={{ content: { color: 'var(--atlas-color-success)' } }}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic
                  title={t('metric.unhealthy')}
                  value={metrics.unhealthy}
                  styles={{ content: { color: metrics.unhealthy ? 'var(--atlas-color-danger)' : undefined } }}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic title={t('metric.successRate')} value={rateText(metrics.success_rate)} />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic title={t('metric.p50')} value={formatDuration(metrics.p50)} />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic title={t('metric.p95')} value={formatDuration(metrics.p95)} />
              </Card>
            </Col>
          </Row>

          <Card
            title={t('reportCard.title')}
            extra={
              <Typography.Text type="secondary">
                {t('reportCard.subtitle')}
              </Typography.Text>
            }
          >
            <Table<ReleaseReportSummary>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={crossReports}
              columns={crossReportColumns}
              locale={{ emptyText: t('empty.reports') }}
            />
          </Card>

          <Row gutter={16}>
            <Col span={8}>
              <Card>
                <Statistic title={t('business.autoRefundRate')} value={rateText(metrics.business?.auto_refund_rate ?? null)} />
              </Card>
            </Col>
            <Col span={8}>
              <Card>
                <Statistic title={t('business.manualEscalationRate')} value={rateText(metrics.business?.manual_escalation_rate ?? null)} />
              </Card>
            </Col>
            <Col span={8}>
              <Card>
                <Statistic
                  title={t('business.amountDiffRate')}
                  value={rateText(metrics.business?.refund_amount_diff_rate ?? null)}
                />
              </Card>
            </Col>
          </Row>

          <Card title={t('business.cardTitle')}>
            <Table
              size="small"
              pagination={false}
              rowKey={(row) => `${row.graph_id}@${row.resolved_version ?? 'draft'}`}
              dataSource={metrics.business?.per_version ?? []}
              locale={{ emptyText: t('empty.business') }}
              columns={[
                { title: t('col.graph'), dataIndex: 'graph_id' },
                {
                  title: t('col.version'),
                  dataIndex: 'resolved_version',
                  width: 90,
                  render: (value: number | null) => (value === null ? t('version.draft') : `v${value}`),
                },
                { title: t('col.samples'), dataIndex: 'samples', width: 80 },
                { title: t('business.colAutoRefundRate'), dataIndex: 'auto_refund_rate', render: rateText },
                { title: t('business.colManualEscalationRate'), dataIndex: 'manual_escalation_rate', render: rateText },
                {
                  title: t('business.colAmountDiffRate'),
                  dataIndex: 'refund_amount_diff_rate',
                  render: rateText,
                },
              ]}
            />
          </Card>

          <Card title={t('tool.cardTitle')}>
            <Table<ToolMetricsRow>
              rowKey="tool"
              size="small"
              pagination={false}
              dataSource={metrics.tools ?? []}
              locale={{ emptyText: t('empty.tools') }}
              columns={[
                { title: t('col.tool'), dataIndex: 'tool' },
                { title: t('col.calls'), dataIndex: 'calls', width: 90 },
                {
                  title: t('col.failed'),
                  dataIndex: 'failed',
                  width: 80,
                  render: (value: number) => (value > 0 ? <Tag color="red">{value}</Tag> : 0),
                },
                { title: t('col.simulated'), dataIndex: 'simulated', width: 80 },
                { title: 'P50', dataIndex: 'p50', width: 100, render: formatDuration },
                { title: 'P95', dataIndex: 'p95', width: 100, render: formatDuration },
                {
                  title: t('col.errorCodes'),
                  dataIndex: 'error_codes',
                  render: (codes: Record<string, number>) => {
                    const entries = Object.entries(codes)
                    return entries.length === 0 ? (
                      '—'
                    ) : (
                      <Space wrap size={4}>
                        {entries.map(([code, count]) => (
                          <Tag key={code} color="volcano">
                            {code} ×{count}
                          </Tag>
                        ))}
                      </Space>
                    )
                  },
                },
              ]}
            />
          </Card>

          <Card
            title={
              <Space>
                {t('alertCard.title')}
                <Badge count={unresolvedCount} title={t('header.unresolvedBadge')} />
              </Space>
            }
            extra={
              <Select
                value={statusFilter}
                style={{ width: 140 }}
                onChange={setStatusFilter}
                options={[
                  { value: 'all', label: t('filter.allStatus') },
                  { value: 'open', label: t(ALERT_STATUS_LABELS.open) },
                  { value: 'acknowledged', label: t(ALERT_STATUS_LABELS.acknowledged) },
                  { value: 'resolved', label: t(ALERT_STATUS_LABELS.resolved) },
                ]}
              />
            }
          >
            <OnCallBar canAdmin={canAdmin} onChanged={refresh} />
            <Table
              rowKey="id"
              size="small"
              columns={alertColumns}
              dataSource={visibleAlerts}
              pagination={{ pageSize: 8, showSizeChanger: false }}
              locale={{ emptyText: t('empty.alerts') }}
            />
            <SilenceManager canAdmin={canAdmin} reloadKey={silenceVersion} />
          </Card>

          <AlertChannelCard canAdmin={canAdmin} />

          {rules && canAdmin && (
            <Card
            title={t('rules.cardTitle')}
            extra={
              <AlertRuleTemplateMarket
                canAdmin={canAdmin}
                onApplied={async () => {
                  try {
                    setRules(await getRules())
                  } catch {
                    // 模板已整体替换；本地规则拉取失败时忽略，下次刷新自愈
                  }
                  await refresh()
                }}
              />
            }
          >
              {ruleError && (
                <Alert type="error" showIcon message={t('rules.saveFailed')} description={ruleError} style={{ marginBottom: 12 }} />
              )}
              {ruleSaved && !ruleError && (
                <Alert type="success" showIcon message={t('rules.saved')} style={{ marginBottom: 12 }} />
              )}
              <RuleConfigEditor value={rules} onChange={setRules} />
              <div style={{ marginTop: 16 }}>
                <Button type="primary" onClick={saveRules}>
                  {t('rules.save')}
                </Button>
              </div>
            </Card>
          )}

          <Card
            title={t('runsCard.title')}
            extra={
              <Select
                value={graphFilter}
                style={{ width: 220 }}
                onChange={setGraphFilter}
                options={[
                  { value: 'all', label: t('filter.allGraphs') },
                  ...metrics.per_graph.map((item) => ({
                    value: item.graph_id,
                    label: item.graph_id,
                  })),
                ]}
              />
            }
          >
            <Table
              rowKey="id"
              size="small"
              columns={runColumns}
              dataSource={runs}
              pagination={{ pageSize: 8, showSizeChanger: false }}
              locale={{ emptyText: t('empty.runs') }}
              expandable={{
                rowExpandable: () => true,
                expandedRowRender: (record) => (
                  <Tabs
                    size="small"
                    items={[
                      {
                        key: 'nodes',
                        label: t('trace.tabNodes'),
                        children: record.error ? (
                    <Alert type="error" showIcon message={t('run.uncaughtError', { error: record.error })} />
                  ) : (
                    <Table
                      rowKey="node_id"
                      size="small"
                      pagination={false}
                      columns={[
                        { title: t('col.node'), dataIndex: 'node_id' },
                        { title: t('col.type'), dataIndex: 'node_type' },
                        {
                          title: t('col.status'),
                          dataIndex: 'status',
                          width: 90,
                          render: (status: string) =>
                            status === 'failed' ? (
                              <Tag color="red">{t('nodeResult.failed')}</Tag>
                            ) : (
                              <Tag color="green">{t('nodeResult.success')}</Tag>
                            ),
                        },
                        { title: t('col.error'), dataIndex: 'error', render: (value: string | null) => value ?? '—' },
                      ]}
                      dataSource={record.nodes}
                    />
                        ),
                      },
                      {
                        key: 'trace',
                        label: t('trace.tabTimeline'),
                        children: <TraceWaterfall runId={record.id} />,
                      },
                    ]}
                  />
                ),
              }}
            />
          </Card>
          <ShadowRunsCard canOperate={canOperate} />
          <WebhookReliabilityCard canOperate={canOperate} canAdmin={canAdmin} />
        </Space>
      </Content>
    </Layout>
  )
}
