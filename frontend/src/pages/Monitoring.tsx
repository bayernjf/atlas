import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
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
  resolveAlert,
  updateRules,
  type AlertItem,
  type AlertStatus,
  type MetricsSummary,
  type RuleConfig,
  type RunRecord,
} from '../lib/apiClient'
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
  const [statusFilter, setStatusFilter] = useState<'all' | AlertStatus>('all')
  const [graphFilter, setGraphFilter] = useState<'all' | string>('all')
  const [loadError, setLoadError] = useState('')
  const [ruleError, setRuleError] = useState('')
  const [ruleSaved, setRuleSaved] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [metricsData, alertsData, runsData] = await Promise.all([
        getMetrics(),
        listAlerts(),
        getRuns(graphFilter === 'all' ? undefined : graphFilter),
      ])
      setMetrics(metricsData)
      setAlerts(alertsData)
      setRuns(runsData)
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
          <span>{ruleLabel(ruleId)}</span>
          <Tag color={SEVERITY_COLORS[alert.severity]} style={{ marginTop: 2 }}>
            {alert.severity === 'critical' ? '严重' : '警告'}
          </Tag>
        </Space>
      ),
    },
    { title: '图', dataIndex: 'graph_id', width: 140 },
    { title: '内容', dataIndex: 'message' },
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
    { title: '图', dataIndex: 'graph_id', width: 140 },
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
