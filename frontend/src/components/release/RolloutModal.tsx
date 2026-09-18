import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Divider,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  getRollout,
  getRuns,
  listVersions,
  promoteRollout,
  rollbackRollout,
  runGraph,
  startRollout,
  updateRollout,
  type RolloutConfig,
  type RolloutSnapshot,
  type RunInputs,
} from '../../lib/apiClient'
import {
  GATE_METRIC_SPECS,
  ROLLOUT_STATUS_META,
  defaultRolloutConfig,
  findRule,
  rolloutActions,
  setGateMetric,
  withRule,
} from '../../lib/release'

const { Text } = Typography
const { TextArea } = Input

type Props = {
  open: boolean
  graphId: string | null
  tenant: string
  onClose: () => void
}

const SEGMENT_LABELS: Record<string, string> = {
  internal: '内部租户',
  lowValueBucket: '低金额桶',
  canary: '百分比灰度',
  full: '全量',
  fallback: '回退 stable',
}

/**
 * 灰度发布 Modal（M9，U59 ②；03 `rollout_config`、04 §5.16、19 §2.3.3）。
 * 四段规则表单 + gate 三指标阈值/观察窗/自动回滚 + start/promote/rollback
 * 状态机与流量计数；附「模拟入站事件」用于沙盘验证三段分桶（D32 沙盘，无真实 ingress）。
 */
export function RolloutModal({ open, graphId, tenant, onClose }: Props) {
  const [snapshot, setSnapshot] = useState<RolloutSnapshot | null>(null)
  const [versions, setVersions] = useState<number[]>([])
  const [draft, setDraft] = useState<RolloutConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [channel, setChannel] = useState<'api' | 'webhook' | 'im' | 'embed'>('webhook')
  const [payloadText, setPayloadText] = useState(
    JSON.stringify({ order_id: '12347', amount: 128 }, null, 2),
  )
  const [lastResolved, setLastResolved] = useState<number | null | undefined>(undefined)

  const refresh = useCallback(async () => {
    if (!graphId) return
    setLoading(true)
    setError(null)
    try {
      const [state, versionList] = await Promise.all([
        getRollout(graphId),
        listVersions(graphId),
      ])
      setSnapshot(state)
      setVersions(versionList)
      setDraft(state.config ?? defaultRolloutConfig(tenant))
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError))
    } finally {
      setLoading(false)
    }
  }, [graphId, tenant])

  useEffect(() => {
    if (open) {
      setLastResolved(undefined)
      void refresh()
    }
  }, [open, refresh])

  if (!draft) {
    return (
      <Modal title="灰度发布" open={open} onCancel={onClose} footer={<Button onClick={onClose}>关闭</Button>}>
        {error ? <Alert type="error" showIcon message={error} /> : <Text>加载中…</Text>}
      </Modal>
    )
  }

  const status = snapshot?.status ?? 'idle'
  const actions = rolloutActions(status, versions.length)
  const rulesLocked = status !== 'idle'
  const internal = findRule(draft, 'internal')
  const bucket = findRule(draft, 'lowValueBucket')
  const canary = findRule(draft, 'canary')

  const runAction = async (key: string, fn: () => Promise<RolloutSnapshot>) => {
    setBusy(key)
    setError(null)
    try {
      setSnapshot(await fn())
      await refresh()
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : String(actionError))
    } finally {
      setBusy(null)
    }
  }

  const saveConfig = () =>
    runAction('save', async () => {
      const state = await updateRollout(graphId as string, draft)
      setSaved(true)
      return state
    })

  const sendEvent = async () => {
    if (!graphId) return
    setBusy('event')
    setError(null)
    try {
      let payload: Record<string, unknown> = {}
      try {
        payload = payloadText.trim() ? (JSON.parse(payloadText) as Record<string, unknown>) : {}
      } catch {
        throw new Error('事件 payload 不是合法 JSON')
      }
      await runGraph(graphId, payload as RunInputs, { event: { channel, payload } })
      const latest = await getRuns(graphId, 1)
      setLastResolved(latest[0]?.resolved_version ?? null)
      await refresh()
    } catch (eventError) {
      setError(eventError instanceof Error ? eventError.message : String(eventError))
    } finally {
      setBusy(null)
    }
  }

  const trafficRows = [
    { key: 'stable', label: 'stable（当前稳定版）', value: snapshot?.traffic.stable ?? 0 },
    { key: 'candidate', label: 'candidate（灰度新版）', value: snapshot?.traffic.candidate ?? 0 },
    ...Object.entries(snapshot?.traffic.segments ?? {}).map(([key, value]) => ({
      key,
      label: SEGMENT_LABELS[key] ?? key,
      value: value ?? 0,
    })),
  ]

  return (
    <Modal
      title={`灰度发布${graphId ? `：${graphId}` : ''}`}
      open={open}
      width={820}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon message={error} />}
        {saved && !error && <Alert type="success" showIcon message="灰度配置已保存" />}

        <Descriptions size="small" column={3} bordered>
          <Descriptions.Item label="状态">
            <Tag color={ROLLOUT_STATUS_META[status].color}>{ROLLOUT_STATUS_META[status].label}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="stable 版本">{snapshot?.stable ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="candidate 版本">{snapshot?.candidate ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="已发布版本" span={3}>
            {versions.length ? versions.map((v) => `v${v}`).join('、') : '尚无发布版'}
            {versions.length < 2 && (
              <Text type="warning">（启动 canary 需至少 2 个发布版）</Text>
            )}
          </Descriptions.Item>
          {snapshot?.rollbackReason && (
            <Descriptions.Item label="回滚原因" span={3}>
              <Text type={snapshot.rollbackActor === 'auto' ? 'danger' : undefined}>
                {snapshot.rollbackActor === 'auto' ? '【自动回滚】' : '【手动回滚】'}
                {snapshot.rollbackReason}
              </Text>
            </Descriptions.Item>
          )}
        </Descriptions>

        <Space wrap>
          <Button
            type="primary"
            disabled={!actions.canStart}
            loading={busy === 'start'}
            onClick={() => runAction('start', () => startRollout(graphId as string))}
          >
            启动 canary
          </Button>
          <Button
            disabled={!actions.canPromote}
            loading={busy === 'promote'}
            onClick={() => runAction('promote', () => promoteRollout(graphId as string))}
          >
            放量到全量（手动 promote）
          </Button>
          <Button
            danger
            disabled={!actions.canRollback}
            loading={busy === 'rollback'}
            onClick={() => runAction('rollback', () => rollbackRollout(graphId as string))}
          >
            回滚到 stable
          </Button>
          <Button loading={loading} onClick={() => void refresh()}>刷新</Button>
        </Space>

        <div>
          <Text strong>流量计数（自本次 canary 起）</Text>
          <Space wrap size="large" style={{ marginTop: 8 }}>
            {trafficRows.map((row) => (
              <Statistic key={row.key} title={row.label} value={row.value} />
            ))}
          </Space>
        </div>

        <Divider style={{ margin: '8px 0' }} />

        <div>
          <Text strong>路由规则</Text>
          {rulesLocked && (
            <Text type="secondary">（canary 进行中/已结束，规则段锁定；如需调整请回滚后重新配置）</Text>
          )}
          <Space orientation="vertical" size={8} style={{ width: '100%', marginTop: 8 }}>
            <Space wrap>
              <Switch
                checked={!!internal}
                disabled={rulesLocked}
                onChange={(checked) =>
                  setDraft(
                    withRule(
                      draft,
                      'internal',
                      checked ? { to: 'internal', tenants: internal?.tenants ?? [tenant] } : null,
                    ),
                  )
                }
              />
              <Text>内部租户：命中租户事件全量走 candidate</Text>
              <Input
                style={{ width: 220 }}
                disabled={rulesLocked || !internal}
                value={(internal?.tenants ?? []).join(',')}
                placeholder="租户 id，逗号分隔"
                onChange={(event) =>
                  internal &&
                  setDraft(
                    withRule(
                      draft,
                      'internal',
                      {
                        to: 'internal',
                        tenants: event.target.value.split(',').map((value) => value.trim()).filter(Boolean),
                      },
                    ),
                  )
                }
              />
            </Space>

            <Space wrap>
              <Switch
                checked={!!bucket}
                disabled={rulesLocked}
                onChange={(checked) =>
                  setDraft(
                    withRule(
                      draft,
                      'lowValueBucket',
                      checked
                        ? { to: 'lowValueBucket', field: 'payload.amount', op: '<=', value: bucket?.value ?? 200, percent: bucket?.percent ?? 100 }
                        : null,
                    ),
                  )
                }
              />
              <Text>低金额桶：payload.amount ≤</Text>
              <InputNumber
                style={{ width: 110 }}
                min={0}
                disabled={rulesLocked || !bucket}
                value={bucket?.value}
                onChange={(value) =>
                  bucket && value !== null &&
                  setDraft(withRule(draft, 'lowValueBucket', { ...bucket, value }))
                }
              />
              <Text>元，按稳定哈希放行</Text>
              <InputNumber
                style={{ width: 90 }}
                min={1}
                max={100}
                suffix="%"
                disabled={rulesLocked || !bucket}
                value={bucket?.percent}
                onChange={(value) =>
                  bucket && value !== null &&
                  setDraft(withRule(draft, 'lowValueBucket', { ...bucket, percent: value }))
                }
              />
            </Space>

            <Space wrap>
              <Switch
                checked={!!canary}
                disabled={rulesLocked}
                onChange={(checked) =>
                  setDraft(
                    withRule(draft, 'canary', checked ? { to: 'canary', percent: canary?.percent ?? 5 } : null),
                  )
                }
              />
              <Text>百分比灰度（订单 id 稳定哈希，同对象不跳版本）</Text>
              <InputNumber
                style={{ width: 90 }}
                min={1}
                max={100}
                suffix="%"
                disabled={rulesLocked || !canary}
                value={canary?.percent}
                onChange={(value) =>
                  canary && value !== null &&
                  setDraft(withRule(draft, 'canary', { ...canary, percent: value }))
                }
              />
            </Space>
            <Text type="secondary">其余流量落 stable；promote 到全量后所有流量走 candidate（full 段，无需配置）。</Text>
          </Space>
        </div>

        <Divider style={{ margin: '8px 0' }} />

        <div>
          <Text strong>门控（越阈自动回滚，仅告警不自动放量）</Text>
          <Space wrap size="large" style={{ marginTop: 8 }}>
            <Space>
              <Text>观察窗</Text>
              <InputNumber
                style={{ width: 90 }}
                min={1}
                suffix="分钟"
                value={draft.gate.observeMinutes}
                onChange={(value) =>
                  value !== null && setDraft({ ...draft, gate: { ...draft.gate, observeMinutes: value } })
                }
              />
            </Space>
            <Space>
              <Text>最少样本</Text>
              <InputNumber
                style={{ width: 80 }}
                min={1}
                value={draft.gate.minSamples}
                onChange={(value) =>
                  value !== null && setDraft({ ...draft, gate: { ...draft.gate, minSamples: value } })
                }
              />
            </Space>
            <Space>
              <Text>越阈自动回滚</Text>
              <Switch
                checked={draft.gate.autoRollback}
                onChange={(checked) =>
                  setDraft({ ...draft, gate: { ...draft.gate, autoRollback: checked } })
                }
              />
            </Space>
          </Space>
          <Table
            style={{ marginTop: 8 }}
            size="small"
            pagination={false}
            rowKey="id"
            dataSource={GATE_METRIC_SPECS.map((spec) => ({
              ...spec,
              metric: draft.gate.metrics.find((item) => item.id === spec.id),
            }))}
            columns={[
              { title: '指标', dataIndex: 'label' },
              {
                title: '阈值（严格大于即越阈）',
                dataIndex: 'metric',
                render: (metric, row) => (
                  <InputNumber
                    style={{ width: 120 }}
                    min={0}
                    max={1}
                    step={row.step}
                    value={metric?.threshold ?? row.defaultThreshold}
                    onChange={(value) =>
                      value !== null &&
                      setDraft(
                        setGateMetric(draft, {
                          id: row.id,
                          threshold: value,
                          compareWith: metric?.compareWith ?? null,
                        }),
                      )
                    }
                  />
                ),
              },
              {
                title: '启用',
                dataIndex: 'metric',
                render: (metric) => <Tag color={metric ? 'green' : 'default'}>{metric ? '已纳入门控' : '未纳入'}</Tag>,
              },
            ]}
          />
          <Space style={{ marginTop: 8 }}>
            <Button type="primary" ghost loading={busy === 'save'} onClick={saveConfig}>
              保存灰度配置
            </Button>
          </Space>
        </div>

        <Divider style={{ margin: '8px 0' }} />

        <div>
          <Text strong>模拟入站事件（沙盘验证三段分桶，无真实 webhook/IM ingress）</Text>
          <Space wrap style={{ marginTop: 8 }}>
            <Select
              style={{ width: 130 }}
              value={channel}
              onChange={setChannel}
              options={[
                { value: 'webhook', label: 'webhook' },
                { value: 'api', label: 'api' },
                { value: 'im', label: 'im' },
                { value: 'embed', label: 'embed' },
              ]}
            />
            <Button type="primary" loading={busy === 'event'} onClick={sendEvent}>
              发送一次事件
            </Button>
            {lastResolved !== undefined && (
              <Tag color={lastResolved === snapshot?.candidate ? 'blue' : 'default'}>
                本次事件落 {lastResolved === null ? 'stable/草稿' : `v${lastResolved}`}
              </Tag>
            )}
          </Space>
          <TextArea
            style={{ marginTop: 8, fontFamily: 'monospace' }}
            rows={3}
            value={payloadText}
            onChange={(event) => setPayloadText(event.target.value)}
          />
        </div>
      </Space>
    </Modal>
  )
}
