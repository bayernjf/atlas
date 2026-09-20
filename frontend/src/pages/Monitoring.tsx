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
import {
  ALERT_STATUS_COLORS,
  ALERT_STATUS_LABELS,
  formatDuration,
  formatTime,
  runHealth,
  ruleLabel,
  SEVERITY_COLORS,
} from '../lib/monitoring'

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
        setRuleError(`存在名称为空的自定义规则（cid ${rule.cid}）`)
        return
      }
      const exprErrors = validateExpression(rule.expression)
      if (exprErrors.length > 0) {
        setRuleError(`自定义规则「${rule.name}」表达式非法：${exprErrors.join('；')}`)
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
      title: '规则',
      dataIndex: 'rule_id',
      width: 110,
      render: (ruleId: AlertItem['rule_id'], alert) => (
        <Space orientation="vertical" size={0}>
          <span>{ruleLabel(ruleId, alert.rule_name)}</span>
          <Tag color={SEVERITY_COLORS[alert.severity]} style={{ marginTop: 2 }}>
            {alert.severity === 'critical' ? '严重' : '警告'}
          </Tag>
        </Space>
      ),
    },
    { title: '图', dataIndex: 'graph_id', width: 140 },
    {
      title: '内容',
      key: 'message',
      render: (_, alert) => (
        <Space orientation="vertical" size={2}>
          <span>{alert.message}</span>
          {alert.action?.type === 'rollback' && (
            <Tag color="volcano" style={{ marginTop: 2 }}>
              {alert.action.actor === 'auto' ? '自动' : '手动'}回滚 v{alert.action.from_version} → v
              {alert.action.to_version}
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: '次数',
      dataIndex: 'count',
      width: 70,
      sorter: (a, b) => a.count - b.count,
    },
    {
      title: '最近运行',
      dataIndex: 'last_run_id',
      width: 90,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (status: AlertStatus) => (
        <Tag color={ALERT_STATUS_COLORS[status]}>{ALERT_STATUS_LABELS[status]}</Tag>
      ),
    },
    {
      title: '最近发生',
      dataIndex: 'last_seen',
      width: 100,
      render: (value: string) => formatTime(value),
    },
    {
      title: '操作',
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
              确认
            </Button>
            <Button
              size="small"
              type="primary"
              ghost
              disabled={alert.status === 'resolved'}
              onClick={() => mutateAlert(resolveAlert, alert.id)}
            >
              关闭
            </Button>
          </Space>
        ) : null,
    },
  ]

  const runColumns: ColumnsType<RunRecord> = [
    { title: '运行', dataIndex: 'id', width: 90 },
    { title: '图', dataIndex: 'graph_id', width: 130 },
    {
      title: '版本',
      dataIndex: 'resolved_version',
      width: 80,
      render: (value: number | null) => (value === null ? '草稿' : `v${value}`),
    },
    {
      title: '方式',
      dataIndex: 'mode',
      width: 80,
      render: (mode: string) => (mode === 'sync' ? '同步' : '流式'),
    },
    {
      title: '状态',
      key: 'health',
      width: 90,
      render: (_, record) => {
        const health = runHealth(record)
        if (health === 'error') return <Tag color="red">异常</Tag>
        if (health === 'unhealthy') return <Tag color="orange">节点失败</Tag>
        return <Tag color="green">健康</Tag>
      },
    },
    {
      title: '节点',
      key: 'nodes',
      render: (_, record) => {
        const failed = record.nodes.filter((node) => node.status === 'failed').length
        return `${record.nodes.length - failed} 成 / ${failed} 败`
      },
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 100,
      render: (value: number) => formatDuration(value),
    },
    {
      title: '开始',
      dataIndex: 'started_at',
      width: 110,
      render: (value: string) => formatTime(value),
    },
  ]

  const crossReportColumns: ColumnsType<ReleaseReportSummary> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 180,
      render: (value) => formatTime(value as string),
    },
    { title: '图 ID', dataIndex: 'graph_id', ellipsis: true },
    {
      title: '触发',
      dataIndex: 'trigger',
      width: 100,
      render: (value) => (value === 'publish-gate' ? '发布门禁' : '手动门禁'),
    },
    {
      title: '通过率',
      dataIndex: 'pass_rate',
      width: 110,
      render: (value) => {
        const rate = value as number | null
        if (rate === null) return <Tag>未覆盖</Tag>
        const color = rate >= 1 ? 'green' : rate >= 0.8 ? 'orange' : 'red'
        return <Tag color={color}>{(rate * 100).toFixed(1)}%</Tag>
      },
    },
    {
      title: '通过/总数',
      width: 100,
      render: (_, record) => `${record.passed}/${record.total}`,
    },
    {
      title: '阻塞',
      dataIndex: 'blocked',
      width: 90,
      render: (value) =>
        value ? <Tag color="red">阻塞</Tag> : <Tag color="green">放行</Tag>,
    },
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header">
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            监控告警
          </Typography.Title>
          <Space>
            <Badge count={unresolvedCount} title="未关闭告警">
              <Button onClick={refresh}>刷新</Button>
            </Badge>
            <Button onClick={onBack}>返回 Dashboard</Button>
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
                <Statistic title="总运行" value={metrics.total} />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic
                  title="健康"
                  value={metrics.healthy}
                  styles={{ content: { color: 'var(--atlas-color-success)' } }}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic
                  title="不健康"
                  value={metrics.unhealthy}
                  styles={{ content: { color: metrics.unhealthy ? 'var(--atlas-color-danger)' : undefined } }}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic title="成功率" value={rateText(metrics.success_rate)} />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic title="P50 耗时" value={formatDuration(metrics.p50)} />
              </Card>
            </Col>
            <Col span={4}>
              <Card>
                <Statistic title="P95 耗时" value={formatDuration(metrics.p95)} />
              </Card>
            </Col>
          </Row>

          <Card
            title="跨图用例集报告（最近 100 条，docs/28 §2.4）"
            extra={
              <Typography.Text type="secondary">
                手动/发布门禁沉淀 · 倒序 · Demo 进程内数据
              </Typography.Text>
            }
          >
            <Table<ReleaseReportSummary>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={crossReports}
              columns={crossReportColumns}
              locale={{ emptyText: '暂无批量回放报告' }}
            />
          </Card>

          <Row gutter={16}>
            <Col span={8}>
              <Card>
                <Statistic title="自动退款率（业务）" value={rateText(metrics.business?.auto_refund_rate ?? null)} />
              </Card>
            </Col>
            <Col span={8}>
              <Card>
                <Statistic title="人工升级率（业务）" value={rateText(metrics.business?.manual_escalation_rate ?? null)} />
              </Card>
            </Col>
            <Col span={8}>
              <Card>
                <Statistic
                  title="退款金额差异率（业务）"
                  value={rateText(metrics.business?.refund_amount_diff_rate ?? null)}
                />
              </Card>
            </Col>
          </Row>

          <Card title="业务指标 · 按发布版本（灰度门控对照，04 §5.13）">
            <Table
              size="small"
              pagination={false}
              rowKey={(row) => `${row.graph_id}@${row.resolved_version ?? 'draft'}`}
              dataSource={metrics.business?.per_version ?? []}
              locale={{ emptyText: '暂无带业务结果的运行' }}
              columns={[
                { title: '图', dataIndex: 'graph_id' },
                {
                  title: '版本',
                  dataIndex: 'resolved_version',
                  width: 90,
                  render: (value: number | null) => (value === null ? '草稿' : `v${value}`),
                },
                { title: '样本', dataIndex: 'samples', width: 80 },
                { title: '自动退款率', dataIndex: 'auto_refund_rate', render: rateText },
                { title: '人工升级率', dataIndex: 'manual_escalation_rate', render: rateText },
                {
                  title: '退款金额差异率',
                  dataIndex: 'refund_amount_diff_rate',
                  render: rateText,
                },
              ]}
            />
          </Card>

          <Card
            title="适配器调用（docs/28 §4.1，真实运行埋点；SIMULATED 为本地模拟，不纳延迟分位）"
          >
            <Table<ToolMetricsRow>
              rowKey="tool"
              size="small"
              pagination={false}
              dataSource={metrics.tools ?? []}
              locale={{ emptyText: '暂无工具调用（运行含工具节点的图后出现）' }}
              columns={[
                { title: '工具（adapter/capability）', dataIndex: 'tool' },
                { title: '调用数', dataIndex: 'calls', width: 90 },
                {
                  title: '失败',
                  dataIndex: 'failed',
                  width: 80,
                  render: (value: number) => (value > 0 ? <Tag color="red">{value}</Tag> : 0),
                },
                { title: '模拟', dataIndex: 'simulated', width: 80 },
                { title: 'P50', dataIndex: 'p50', width: 100, render: formatDuration },
                { title: 'P95', dataIndex: 'p95', width: 100, render: formatDuration },
                {
                  title: '错误码分布',
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
                告警
                <Badge count={unresolvedCount} title="未关闭告警" />
              </Space>
            }
            extra={
              <Select
                value={statusFilter}
                style={{ width: 140 }}
                onChange={setStatusFilter}
                options={[
                  { value: 'all', label: '全部状态' },
                  { value: 'open', label: '待处理' },
                  { value: 'acknowledged', label: '已确认' },
                  { value: 'resolved', label: '已关闭' },
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
              locale={{ emptyText: '暂无告警' }}
            />
          </Card>

          {rules && canAdmin && (
            <Card title="告警规则（仅管理员可改，阈值调整即时生效，进程内保存）">
              {ruleError && (
                <Alert type="error" showIcon message="规则保存失败" description={ruleError} style={{ marginBottom: 12 }} />
              )}
              {ruleSaved && !ruleError && (
                <Alert type="success" showIcon message="规则已保存" style={{ marginBottom: 12 }} />
              )}
              <Space wrap size="large">
                <Space>
                  <span>运行异常</span>
                  <Switch
                    checked={rules.run_error.enabled}
                    onChange={(enabled) =>
                      setRules({ ...rules, run_error: { enabled } })
                    }
                  />
                </Space>
                <Space>
                  <span>节点失败</span>
                  <Switch
                    checked={rules.node_failed.enabled}
                    onChange={(enabled) =>
                      setRules({ ...rules, node_failed: { enabled } })
                    }
                  />
                </Space>
                <Space>
                  <span>连续失败</span>
                  <Switch
                    checked={rules.consecutive_failures.enabled}
                    onChange={(enabled) =>
                      setRules({
                        ...rules,
                        consecutive_failures: { ...rules.consecutive_failures, enabled },
                      })
                    }
                  />
                  <span>阈值</span>
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
                    suffix="次"
                  />
                </Space>
                <Space>
                  <span>失败率</span>
                  <Switch
                    checked={rules.failure_rate.enabled}
                    onChange={(enabled) =>
                      setRules({
                        ...rules,
                        failure_rate: { ...rules.failure_rate, enabled },
                      })
                    }
                  />
                  <span>窗口</span>
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
                    suffix="次"
                  />
                  <span>最少样本</span>
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
                  <span>比率</span>
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
                  保存规则
                </Button>
              </Space>

              <div style={{ marginTop: 16 }}>
                <Typography.Text strong>自定义规则（表达式，docs/28 §4.2）</Typography.Text>
                <Typography.Paragraph type="secondary" style={{ marginBottom: 8, marginTop: 4 }}>
                  可用变量（双花括号引用）：status（completed / error / cancelled）、durationMs（整数毫秒）、
                  failedCount（失败节点数）、hasError（是否有错误）；示例：{"{{status}} == 'error' || {{hasError}}"}。
                  表达式复用安全条件引擎（禁 eval），结果须为布尔，否则运行时 fail-safe 不告警。
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
                        placeholder="规则名称"
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
                          { value: 'warning', label: '警告' },
                          { value: 'critical', label: '严重' },
                        ]}
                      />
                      <Space style={{ marginTop: 4 }}>
                        <span>启用</span>
                        <Switch
                          checked={rule.enabled}
                          onChange={(enabled) => updateCustom(idx, { enabled })}
                        />
                      </Space>
                      <Button danger size="small" style={{ marginTop: 2 }} onClick={() => removeCustom(idx)}>
                        删除
                      </Button>
                      {(nameEmpty || exprErrors.length > 0) && (
                        <Typography.Text type="danger" style={{ marginTop: 6 }}>
                          {nameEmpty ? '名称不能为空' : exprErrors.join('；')}
                        </Typography.Text>
                      )}
                    </Space>
                  )
                })}
                <Button size="small" onClick={addCustom}>
                  ＋ 新增自定义规则
                </Button>
              </div>
            </Card>
          )}

          <Card
            title="最近运行"
            extra={
              <Select
                value={graphFilter}
                style={{ width: 220 }}
                onChange={setGraphFilter}
                options={[
                  { value: 'all', label: '全部图' },
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
              locale={{ emptyText: '暂无运行' }}
              expandable={{
                rowExpandable: (record) => record.nodes.length > 0 || record.error !== null,
                expandedRowRender: (record) =>
                  record.error ? (
                    <Alert type="error" showIcon message={`未捕获错误：${record.error}`} />
                  ) : (
                    <Table
                      rowKey="node_id"
                      size="small"
                      pagination={false}
                      columns={[
                        { title: '节点', dataIndex: 'node_id' },
                        { title: '类型', dataIndex: 'node_type' },
                        {
                          title: '状态',
                          dataIndex: 'status',
                          width: 90,
                          render: (status: string) =>
                            status === 'failed' ? (
                              <Tag color="red">失败</Tag>
                            ) : (
                              <Tag color="green">成功</Tag>
                            ),
                        },
                        { title: '错误', dataIndex: 'error', render: (value: string | null) => value ?? '—' },
                      ]}
                      dataSource={record.nodes}
                    />
                  ),
              }}
            />
          </Card>
        </Space>
      </Content>
    </Layout>
  )
}
