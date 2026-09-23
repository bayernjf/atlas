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
import { useTranslation } from '../../locales'

const { Text } = Typography
const { TextArea } = Input

type Props = {
  open: boolean
  graphId: string | null
  tenant: string
  onClose: () => void
}

/** 流量段 label 为 editor namespace i18n 键（docs/57）；未知段原样显示段 key */
const SEGMENT_LABEL_KEYS: Record<string, string> = {
  internal: 'rollout.segment.internal',
  lowValueBucket: 'rollout.segment.lowValueBucket',
  canary: 'rollout.segment.canary',
  full: 'rollout.segment.full',
  fallback: 'rollout.segment.fallback',
}

/**
 * 灰度发布 Modal（M9，U59 ②；03 `rollout_config`、04 §5.16、19 §2.3.3）。
 * 四段规则表单 + gate 三指标阈值/观察窗/自动回滚 + start/promote/rollback
 * 状态机与流量计数；附「模拟入站事件」用于沙盘验证三段分桶（D32 沙盘，无真实 ingress）。
 */
export function RolloutModal({ open, graphId, tenant, onClose }: Props) {
  const { t, i18n } = useTranslation('editor')
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

  // 打开弹窗时重置上一轮状态（渲染期按 prop 变化重置，避免 effect 内同步 setState）
  const [prevOpen, setPrevOpen] = useState(open)
  if (open !== prevOpen) {
    setPrevOpen(open)
    if (open) {
      setLoading(true)
      setError(null)
      setLastResolved(undefined)
    }
  }

  const refresh = useCallback(async () => {
    if (!graphId) return
    // 加载态由打开弹窗时的渲染期重置与「刷新」按钮的事件处理负责，此处不再同步置位
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
    // 数据获取 effect（fetch-on-mount）：refresh 内 setState 均在 await 之后，无同步级联渲染
    // oxlint-disable-next-line react/set-state-in-effect
    if (open) void refresh()
  }, [open, refresh])

  if (!draft) {
    return (
      <Modal title={t('rollout.title')} open={open} onCancel={onClose} footer={<Button onClick={onClose}>{t('common:button.close')}</Button>}>
        {error ? <Alert type="error" showIcon message={error} /> : <Text>{t('common:status.loading')}</Text>}
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
        throw new Error(t('rollout.payloadInvalid'))
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
    { key: 'stable', label: t('rollout.traffic.stable'), value: snapshot?.traffic.stable ?? 0 },
    { key: 'candidate', label: t('rollout.traffic.candidate'), value: snapshot?.traffic.candidate ?? 0 },
    ...Object.entries(snapshot?.traffic.segments ?? {}).map(([key, value]) => ({
      key,
      label: SEGMENT_LABEL_KEYS[key] ? t(SEGMENT_LABEL_KEYS[key]) : key,
      value: value ?? 0,
    })),
  ]
  const versionListSeparator = i18n.language === 'en-US' ? ', ' : '、'

  return (
    <Modal
      title={graphId ? t('rollout.titleWithGraph', { graphId }) : t('rollout.title')}
      open={open}
      width={820}
      onCancel={onClose}
      footer={<Button onClick={onClose}>{t('common:button.close')}</Button>}
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon message={error} />}
        {saved && !error && <Alert type="success" showIcon message={t('rollout.saved')} />}

        <Descriptions size="small" column={3} bordered>
          <Descriptions.Item label={t('rollout.desc.status')}>
            <Tag color={ROLLOUT_STATUS_META[status].color}>{t(ROLLOUT_STATUS_META[status].label)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label={t('rollout.desc.stableVersion')}>{snapshot?.stable ?? '—'}</Descriptions.Item>
          <Descriptions.Item label={t('rollout.desc.candidateVersion')}>{snapshot?.candidate ?? '—'}</Descriptions.Item>
          <Descriptions.Item label={t('rollout.desc.publishedVersions')} span={3}>
            {versions.length ? versions.map((v) => `v${v}`).join(versionListSeparator) : t('rollout.noPublished')}
            {versions.length < 2 && (
              <Text type="warning">{t('rollout.needTwoVersions')}</Text>
            )}
          </Descriptions.Item>
          {snapshot?.rollbackReason && (
            <Descriptions.Item label={t('rollout.desc.rollbackReason')} span={3}>
              <Text type={snapshot.rollbackActor === 'auto' ? 'danger' : undefined}>
                {snapshot.rollbackActor === 'auto' ? t('rollout.autoRollbackTag') : t('rollout.manualRollbackTag')}
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
            {t('rollout.action.start')}
          </Button>
          <Button
            disabled={!actions.canPromote}
            loading={busy === 'promote'}
            onClick={() => runAction('promote', () => promoteRollout(graphId as string))}
          >
            {t('rollout.action.promote')}
          </Button>
          <Button
            danger
            disabled={!actions.canRollback}
            loading={busy === 'rollback'}
            onClick={() => runAction('rollback', () => rollbackRollout(graphId as string))}
          >
            {t('rollout.action.rollback')}
          </Button>
          <Button loading={loading} onClick={() => { setLoading(true); void refresh() }}>{t('common:button.refresh')}</Button>
        </Space>

        <div>
          <Text strong>{t('rollout.trafficTitle')}</Text>
          <Space wrap size="large" style={{ marginTop: 8 }}>
            {trafficRows.map((row) => (
              <Statistic key={row.key} title={row.label} value={row.value} />
            ))}
          </Space>
        </div>

        <Divider style={{ margin: '8px 0' }} />

        <div>
          <Text strong>{t('rollout.rulesTitle')}</Text>
          {rulesLocked && (
            <Text type="secondary">{t('rollout.rulesLocked')}</Text>
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
              <Text>{t('rollout.internalRule')}</Text>
              <Input
                style={{ width: 220 }}
                disabled={rulesLocked || !internal}
                value={(internal?.tenants ?? []).join(',')}
                placeholder={t('rollout.tenantsPlaceholder')}
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
              <Text>{t('rollout.bucketRule')}</Text>
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
              <Text>{t('rollout.bucketHash')}</Text>
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
              <Text>{t('rollout.canaryRule')}</Text>
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
            <Text type="secondary">{t('rollout.restHint')}</Text>
          </Space>
        </div>

        <Divider style={{ margin: '8px 0' }} />

        <div>
          <Text strong>{t('rollout.gateTitle')}</Text>
          <Space wrap size="large" style={{ marginTop: 8 }}>
            <Space>
              <Text>{t('rollout.gate.observeWindow')}</Text>
              <InputNumber
                style={{ width: 90 }}
                min={1}
                suffix={t('rollout.gate.minutes')}
                value={draft.gate.observeMinutes}
                onChange={(value) =>
                  value !== null && setDraft({ ...draft, gate: { ...draft.gate, observeMinutes: value } })
                }
              />
            </Space>
            <Space>
              <Text>{t('rollout.gate.minSamples')}</Text>
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
              <Text>{t('rollout.gate.autoRollback')}</Text>
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
              { title: t('rollout.gate.colMetric'), dataIndex: 'label', render: (label: string) => t(label) },
              {
                title: t('rollout.gate.colThreshold'),
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
                title: t('rollout.gate.colEnabled'),
                dataIndex: 'metric',
                render: (metric) => <Tag color={metric ? 'green' : 'default'}>{metric ? t('rollout.included') : t('rollout.excluded')}</Tag>,
              },
            ]}
          />
          <Space style={{ marginTop: 8 }}>
            <Button type="primary" ghost loading={busy === 'save'} onClick={saveConfig}>
              {t('rollout.save')}
            </Button>
          </Space>
        </div>

        <Divider style={{ margin: '8px 0' }} />

        <div>
          <Text strong>{t('rollout.simTitle')}</Text>
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
              {t('rollout.sim.sendEvent')}
            </Button>
            {lastResolved !== undefined && (
              <Tag color={lastResolved === snapshot?.candidate ? 'blue' : 'default'}>
                {lastResolved === null
                  ? t('rollout.eventLandedDraft')
                  : t('rollout.eventLandedVersion', { version: lastResolved })}
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
