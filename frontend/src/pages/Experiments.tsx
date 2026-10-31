import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  InputNumber,
  Layout,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UserBadge } from '../components/UserBadge'
import { roleCan, type Principal } from '../lib/auth'
import {
  concludeExperiment,
  createExperiment,
  getExperiment,
  listExperiments,
  listGraphs,
  listVersions,
  type Experiment,
  type ExperimentDetail,
  type ExperimentMetric,
} from '../lib/experiments'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type ExperimentsProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

const METRICS: ExperimentMetric[] = [
  'run_error_rate',
  'manual_escalation_rate',
  'refund_amount_diff_rate',
]

const STATUS_TONE: Record<string, string> = {
  running: 'processing',
  concluded: 'default',
}

const VERDICT_ALERT: Record<string, 'info' | 'warning' | 'success'> = {
  collecting: 'info',
  inconclusive: 'warning',
  significant: 'success',
}

const rateCell = (count: number, rate: number | null): string =>
  rate === null ? '—' : `${(rate * 100).toFixed(1)}%（${count}）`

/**
 * 打包 BR（docs/134 D77）：A/B 实验管理面。
 * 创建即启动分流（operate）；详情两臂三率对照＋verdict 横幅（建议性）＋conclude
 * 人决两按钮（BR-D4）。列表是 read 面（viewer 可见），写操作页内按角色禁用。
 * 写调用面经 lib/experiments.ts 转发（页面不直接 import apiClient，U1124 机检）。
 */
export function Experiments({ principal, onLogout, onBack }: ExperimentsProps): ReactElement {
  const { t } = useTranslation('experiments')
  const { message } = App.useApp()
  const canOperate = roleCan(principal.role, 'operate')

  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [graphOptions, setGraphOptions] = useState<Array<{ value: string; label: string }>>([])
  const [formGraph, setFormGraph] = useState<string | undefined>(undefined)
  const [versionOptions, setVersionOptions] = useState<Array<{ value: number; label: string }>>([])
  const [formVersionA, setFormVersionA] = useState<number | undefined>(undefined)
  const [formVersionB, setFormVersionB] = useState<number | undefined>(undefined)
  const [formTraffic, setFormTraffic] = useState(50)
  const [formMetric, setFormMetric] = useState<ExperimentMetric>('run_error_rate')
  const [formMinSamples, setFormMinSamples] = useState(30)
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState<ExperimentDetail | null>(null)

  const refresh = useCallback(async () => {
    try {
      setExperiments(await listExperiments())
    } catch (error) {
      message.error(String(error))
    }
  }, [message])

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh()
    listGraphs()
      .then((graphs) => setGraphOptions(graphs.map((g) => ({ value: g.id, label: g.id }))))
      .catch((error) => message.error(String(error)))
  }, [refresh, message])
  /* oxlint-enable react/set-state-in-effect */

  const pickGraph = async (graphId: string) => {
    setFormGraph(graphId)
    setFormVersionA(undefined)
    setFormVersionB(undefined)
    try {
      const versions = await listVersions(graphId)
      setVersionOptions(versions.map((v) => ({ value: v, label: `v${v}` })))
    } catch (error) {
      message.error(String(error))
      setVersionOptions([])
    }
  }

  const create = async () => {
    if (!formGraph || formVersionA === undefined || formVersionB === undefined) return
    if (formVersionA === formVersionB) {
      message.error(t('create.sameVersion'))
      return
    }
    setBusy(true)
    try {
      await createExperiment({
        graphId: formGraph,
        versionA: formVersionA,
        versionB: formVersionB,
        trafficPercent: formTraffic,
        primaryMetric: formMetric,
        minSamples: formMinSamples,
      })
      setFormGraph(undefined)
      setFormVersionA(undefined)
      setFormVersionB(undefined)
      setVersionOptions([])
      await refresh()
      message.success(t('create.created'))
    } catch (error) {
      message.error(String(error))
    } finally {
      setBusy(false)
    }
  }

  const openDetail = async (exp: Experiment) => {
    try {
      setDetail(await getExperiment(exp.id))
    } catch (error) {
      message.error(String(error))
    }
  }

  const conclude = async (action: 'adopt_a' | 'adopt_b') => {
    if (!detail) return
    try {
      await concludeExperiment(detail.id, action)
      setDetail(await getExperiment(detail.id))
      await refresh()
      message.success(t('conclude.done'))
    } catch (error) {
      message.error(String(error))
    }
  }

  const columns: ColumnsType<Experiment> = [
    { title: t('list.id'), dataIndex: 'id', key: 'id' },
    { title: t('list.graph'), dataIndex: 'graphId', key: 'graphId' },
    {
      title: t('list.versions'),
      key: 'versions',
      render: (_, e) => `v${e.versionA} → v${e.versionB}`,
    },
    {
      title: t('list.traffic'),
      key: 'traffic',
      render: (_, e) => `${e.trafficPercent}%`,
    },
    {
      title: t('list.metric'),
      key: 'metric',
      render: (_, e) => <Tag>{t(`metric.${e.primaryMetric}`)}</Tag>,
    },
    {
      title: t('list.status'),
      key: 'status',
      render: (_, e) => (
        <Tag color={STATUS_TONE[e.status] ?? 'default'}>{t(`status.${e.status}`)}</Tag>
      ),
    },
    { title: t('list.createdAt'), dataIndex: 'createdAt', key: 'createdAt' },
    {
      title: t('list.action'),
      key: 'action',
      render: (_, e) => (
        <Button size="small" onClick={() => void openDetail(e)}>
          {t('list.detail')}
        </Button>
      ),
    },
  ]

  const armRows = detail
    ? [
        {
          key: 'samples',
          label: t('detail.rows.samples'),
          a: String(detail.armStats.A.samples),
          b: String(detail.armStats.B.samples),
        },
        {
          key: 'businessSamples',
          label: t('detail.rows.businessSamples'),
          a: String(detail.armStats.A.businessSamples),
          b: String(detail.armStats.B.businessSamples),
        },
        {
          key: 'run_error_rate',
          label: t('metric.run_error_rate'),
          a: rateCell(detail.armStats.A.errorCount, detail.armStats.A.errorRate),
          b: rateCell(detail.armStats.B.errorCount, detail.armStats.B.errorRate),
        },
        {
          key: 'manual_escalation_rate',
          label: t('metric.manual_escalation_rate'),
          a: rateCell(detail.armStats.A.manualCount, detail.armStats.A.manualRate),
          b: rateCell(detail.armStats.B.manualCount, detail.armStats.B.manualRate),
        },
        {
          key: 'refund_amount_diff_rate',
          label: t('metric.refund_amount_diff_rate'),
          a: rateCell(detail.armStats.A.diffCount, detail.armStats.A.diffRate),
          b: rateCell(detail.armStats.B.diffCount, detail.armStats.B.diffRate),
        },
      ]
    : []

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button onClick={onBack}>{t('common:nav.back')}</Button>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          {t('page.title')}
        </Typography.Title>
        <span style={{ flex: 1 }} />
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content style={{ padding: 24 }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          {canOperate && (
            <Card title={t('create.title')}>
              <Space wrap>
                <Select
                  style={{ minWidth: 220 }}
                  value={formGraph}
                  placeholder={t('create.graphPlaceholder')}
                  aria-label={t('create.graphPlaceholder')}
                  options={graphOptions}
                  onChange={(value) => void pickGraph(value)}
                />
                <Select
                  style={{ minWidth: 140 }}
                  value={formVersionA}
                  placeholder={t('create.versionA')}
                  aria-label={t('create.versionA')}
                  options={versionOptions}
                  disabled={!formGraph}
                  onChange={setFormVersionA}
                />
                <Select
                  style={{ minWidth: 140 }}
                  value={formVersionB}
                  placeholder={t('create.versionB')}
                  aria-label={t('create.versionB')}
                  options={versionOptions}
                  disabled={!formGraph}
                  onChange={setFormVersionB}
                />
                <InputNumber
                  min={1}
                  max={99}
                  value={formTraffic}
                  addonAfter="%"
                  placeholder={t('create.traffic')}
                  aria-label={t('create.traffic')}
                  onChange={(value) => setFormTraffic(value ?? 50)}
                />
                <Select
                  style={{ minWidth: 180 }}
                  value={formMetric}
                  aria-label={t('create.metric')}
                  options={METRICS.map((m) => ({ value: m, label: t(`metric.${m}`) }))}
                  onChange={setFormMetric}
                />
                <InputNumber
                  min={1}
                  value={formMinSamples}
                  placeholder={t('create.minSamples')}
                  aria-label={t('create.minSamples')}
                  onChange={(value) => setFormMinSamples(value ?? 30)}
                />
                <Button
                  type="primary"
                  loading={busy}
                  disabled={
                    !formGraph ||
                    formVersionA === undefined ||
                    formVersionB === undefined ||
                    formVersionA === formVersionB
                  }
                  onClick={() => void create()}
                >
                  {t('create.submit')}
                </Button>
              </Space>
            </Card>
          )}
          <Card title={t('list.title')}>
            {experiments.length === 0 ? (
              <Typography.Text type="secondary">{t('list.empty')}</Typography.Text>
            ) : (
              <Table rowKey="id" columns={columns} dataSource={experiments} pagination={false} />
            )}
          </Card>
        </Space>
      </Content>
      <Drawer
        width={720}
        open={detail !== null}
        onClose={() => setDetail(null)}
        title={detail ? `${t('detail.title')} ${detail.id}` : t('detail.title')}
      >
        {detail && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space wrap>
              <Typography.Text strong>{detail.graphId}</Typography.Text>
              <Typography.Text>
                v{detail.versionA} → v{detail.versionB}
              </Typography.Text>
              <Tag>{t(`metric.${detail.primaryMetric}`)}</Tag>
              <Tag color={STATUS_TONE[detail.status] ?? 'default'}>
                {t(`status.${detail.status}`)}
              </Tag>
              <Typography.Text type="secondary">
                {detail.createdBy} · {detail.createdAt}
              </Typography.Text>
            </Space>
            {detail.status === 'running' && !detail.splitActive && (
              <Alert type="warning" showIcon message={t('detail.splitInactive')} />
            )}
            <Alert
              type={VERDICT_ALERT[detail.verdict.status] ?? 'info'}
              showIcon
              message={
                detail.verdict.winner
                  ? `${t(`detail.verdict.${detail.verdict.status}`)} · ${t('detail.winner', { arm: detail.verdict.winner })}`
                  : t(`detail.verdict.${detail.verdict.status}`)
              }
              description={detail.verdict.reason}
            />
            <Typography.Text type="secondary">
              {detail.requiredSamples !== null
                ? t('detail.requiredSamples', { count: detail.requiredSamples })
                : t('detail.requiredSamplesNone')}
            </Typography.Text>
            <Table
              rowKey="key"
              size="small"
              pagination={false}
              dataSource={armRows}
              columns={[
                { title: t('detail.colMetric'), dataIndex: 'label', key: 'label' },
                { title: `A（v${detail.versionA}）`, dataIndex: 'a', key: 'a' },
                { title: `B（v${detail.versionB}）`, dataIndex: 'b', key: 'b' },
              ]}
            />
            {detail.status === 'concluded' && detail.conclusionAction && (
              <Typography.Text type="secondary">
                {t('detail.concludedLine', {
                  action: t(`detail.action.${detail.conclusionAction}`),
                  time: detail.concludedAt ?? '',
                })}
              </Typography.Text>
            )}
            {canOperate && detail.status === 'running' && (
              <Space>
                <Popconfirm
                  title={t('conclude.confirmA')}
                  onConfirm={() => void conclude('adopt_a')}
                >
                  <Button>{t('conclude.adoptA')}</Button>
                </Popconfirm>
                <Popconfirm
                  title={t('conclude.confirmB')}
                  onConfirm={() => void conclude('adopt_b')}
                >
                  <Button type="primary">{t('conclude.adoptB')}</Button>
                </Popconfirm>
              </Space>
            )}
          </Space>
        )}
      </Drawer>
    </Layout>
  )
}
