import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Input,
  InputNumber,
  Layout,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
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
  type CustomRuleConfig,
  type MetricsSummary,
  type ReleaseReportSummary,
  type RuleConfig,
  type RunRecord,
  type ToolMetricsRow,
} from '../lib/apiClient'
import { validateExpression } from '../lib/conditions'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import { ShadowRunsCard } from '../components/shadow/ShadowRunsCard'
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

  const updateCustom = (idx: number, patch: Partial<CustomRuleConfig>) => {
    setRules((current) =>
      !current
        ? current
        : {
            ...current,
            custom: (current.custom ?? []).map((rule, i) =>
              i === idx ? { ...rule, ...patch } : rule,
            ),
          },
    )
  }
  const addCustom = () => {
    setRules((current) =>
      !current
        ? current
        : {
            ...current,
            custom: [
              ...(current.custom ?? []),
              {
                cid: crypto.randomUUID(),
                name: '',
                enabled: true,
                expression: '{{hasError}}',
                severity: 'warning',
              },
            ],
          },
    )
  }
  const removeCustom = (idx: number) => {
    setRules((current) =>
      !current
        ? current
        : { ...current, custom: (current.custom ?? []).filter((_, i) => i !== idx) },
    )
  }

  const saveRules = async () => {
    if (!rules) return
    setRuleError('')
    setRuleSaved(false)
    // docs/28 §4.2：保存前逐行前端校验（与后端 validate_rules 同构，禁 eval 引擎）
    for (const rule of rules.custom ?? []) {
      if (!rule.name.trim()) {
        setRuleError(t('rules.nameEmpty', { cid: rule.cid }))
        return
      }
      const exprErrors = validateExpression(rule.expression)
      if (exprErrors.length > 0) {
        setRuleError(t('rules.exprInvalid', { name: rule.name, errors: exprErrors.join('；') }))
        return
      }
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
        </Space>
      ),
    },
    { title: t('col.graph'), dataIndex: 'graph_id', width: 140 },
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
      width: 140,
      render: (_, alert) =>
        canOperate ? (
          <Space>
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
            <Table
              rowKey="id"
              size="small"
              columns={alertColumns}
              dataSource={visibleAlerts}
              pagination={{ pageSize: 8, showSizeChanger: false }}
              locale={{ emptyText: t('empty.alerts') }}
            />
          </Card>

          {rules && canAdmin && (
            <Card title={t('rules.cardTitle')}>
              {ruleError && (
                <Alert type="error" showIcon message={t('rules.saveFailed')} description={ruleError} style={{ marginBottom: 12 }} />
              )}
              {ruleSaved && !ruleError && (
                <Alert type="success" showIcon message={t('rules.saved')} style={{ marginBottom: 12 }} />
              )}
              <Space wrap size="large">
                <Space>
                  <span>{t('builtinRule.runError')}</span>
                  <Switch
                    checked={rules.run_error.enabled}
                    onChange={(enabled) =>
                      setRules({ ...rules, run_error: { enabled } })
                    }
                  />
                </Space>
                <Space>
                  <span>{t('builtinRule.nodeFailed')}</span>
                  <Switch
                    checked={rules.node_failed.enabled}
                    onChange={(enabled) =>
                      setRules({ ...rules, node_failed: { enabled } })
                    }
                  />
                </Space>
                <Space>
                  <span>{t('builtinRule.consecutiveFailures')}</span>
                  <Switch
                    checked={rules.consecutive_failures.enabled}
                    onChange={(enabled) =>
                      setRules({
                        ...rules,
                        consecutive_failures: { ...rules.consecutive_failures, enabled },
                      })
                    }
                  />
                  <span>{t('ruleForm.threshold')}</span>
                  <InputNumber
                    min={1}
                    max={200}
                    value={rules.consecutive_failures.threshold}
                    onChange={(value) =>
                      value !== null &&
                      setRules({
                        ...rules,
                        consecutive_failures: { ...rules.consecutive_failures, threshold: value },
                      })
                    }
                    suffix={t('ruleForm.unitTimes')}
                  />
                </Space>
                <Space>
                  <span>{t('ruleForm.failureRate')}</span>
                  <Switch
                    checked={rules.failure_rate.enabled}
                    onChange={(enabled) =>
                      setRules({
                        ...rules,
                        failure_rate: { ...rules.failure_rate, enabled },
                      })
                    }
                  />
                  <span>{t('ruleForm.window')}</span>
                  <InputNumber
                    min={1}
                    max={200}
                    value={rules.failure_rate.window}
                    onChange={(value) =>
                      value !== null &&
                      setRules({
                        ...rules,
                        failure_rate: { ...rules.failure_rate, window: value },
                      })
                    }
                    suffix={t('ruleForm.unitTimes')}
                  />
                  <span>{t('ruleForm.minSamples')}</span>
                  <InputNumber
                    min={1}
                    max={200}
                    value={rules.failure_rate.min_samples}
                    onChange={(value) =>
                      value !== null &&
                      setRules({
                        ...rules,
                        failure_rate: { ...rules.failure_rate, min_samples: value },
                      })
                    }
                  />
                  <span>{t('ruleForm.rate')}</span>
                  <InputNumber
                    min={0}
                    max={1}
                    step={0.05}
                    value={rules.failure_rate.rate}
                    onChange={(value) =>
                      value !== null &&
                      setRules({
                        ...rules,
                        failure_rate: { ...rules.failure_rate, rate: value },
                      })
                    }
                  />
                </Space>
                <Button type="primary" onClick={saveRules}>
                  {t('rules.save')}
                </Button>
              </Space>

              <div style={{ marginTop: 16 }}>
                <Typography.Text strong>{t('custom.title')}</Typography.Text>
                <Typography.Paragraph type="secondary" style={{ marginBottom: 8, marginTop: 4 }}>
                  {/* custom.hint 内含教学用字面 {{status}}/{{hasError}}，t() 不传这两个变量故原样保留 */}
                  {t('custom.hint')}
                </Typography.Paragraph>
                {(rules.custom ?? []).map((rule, idx) => {
                  const exprErrors = validateExpression(rule.expression)
                  const nameEmpty = !rule.name.trim()
                  return (
                    <Space
                      key={rule.cid}
                      wrap
                      align="start"
                      style={{ display: 'flex', marginBottom: 8 }}
                    >
                      <Input
                        placeholder={t('custom.namePlaceholder')}
                        value={rule.name}
                        style={{ width: 150 }}
                        status={nameEmpty ? 'error' : undefined}
                        onChange={(event) => updateCustom(idx, { name: event.target.value })}
                      />
                      <Input
                        placeholder="{{status}} == 'error' || {{hasError}}"
                        value={rule.expression}
                        style={{ width: 340, fontFamily: 'monospace' }}
                        status={exprErrors.length > 0 ? 'error' : undefined}
                        onChange={(event) => updateCustom(idx, { expression: event.target.value })}
                      />
                      <Select
                        value={rule.severity}
                        style={{ width: 100 }}
                        onChange={(severity) => updateCustom(idx, { severity })}
                        options={[
                          { value: 'warning', label: t('severity.warning') },
                          { value: 'critical', label: t('severity.critical') },
                        ]}
                      />
                      <Space style={{ marginTop: 4 }}>
                        <span>{t('custom.enabled')}</span>
                        <Switch
                          checked={rule.enabled}
                          onChange={(enabled) => updateCustom(idx, { enabled })}
                        />
                      </Space>
                      <Button danger size="small" style={{ marginTop: 2 }} onClick={() => removeCustom(idx)}>
                        {t('common:button.delete')}
                      </Button>
                      {(nameEmpty || exprErrors.length > 0) && (
                        <Typography.Text type="danger" style={{ marginTop: 6 }}>
                          {nameEmpty ? t('custom.nameEmpty') : exprErrors.join('；')}
                        </Typography.Text>
                      )}
                    </Space>
                  )
                })}
                <Button size="small" onClick={addCustom}>
                  {t('custom.add')}
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
                rowExpandable: (record) => record.nodes.length > 0 || record.error !== null,
                expandedRowRender: (record) =>
                  record.error ? (
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
              }}
            />
          </Card>
          <ShadowRunsCard canOperate={canOperate} />
        </Space>
      </Content>
    </Layout>
  )
}
