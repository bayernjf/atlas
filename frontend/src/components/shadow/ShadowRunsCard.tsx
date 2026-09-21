import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Empty, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  compareShadowRun,
  listShadowRuns,
  type ShadowRun,
} from '../../lib/apiClient'
import {
  HUMAN_ACTION_VALUES,
  autoActionLabel,
  comparisonVerdict,
} from '../../lib/shadow'
import { formatTime } from '../../lib/monitoring'
import { useTranslation } from '../../locales'

const { Text } = Typography

const VERDICT_COLORS = {
  consistent: 'green',
  mismatch: 'red',
  pending: 'default',
} as const

type Props = {
  /** operate/admin 才显示补录人工结果的写控件（后端另有 RBAC，viewer 只读）。 */
  canOperate: boolean
}

/**
 * D26 影子运行监控 Card（docs/33 §3、§7）：只读列出最近影子运行及系统/人工对比结论，
 * operate/admin 可事后补录人工实际处理触发重算。进程内 ring 100/租户，v1 手动刷新。
 */
export function ShadowRunsCard({ canOperate }: Props) {
  const { t } = useTranslation('monitoring')
  const [runs, setRuns] = useState<ShadowRun[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [submittingId, setSubmittingId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setRuns(await listShadowRuns())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  // 首次加载：setState 全部在 Promise 落定后（异步），避免 effect 内同步 setState 级联渲染；
  // 手动刷新走 refresh（事件回调，首行 setLoading 合法）。
  useEffect(() => {
    let cancelled = false
    listShadowRuns()
      .then((items) => {
        if (!cancelled) setRuns(items)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function submitOutcome(runId: string) {
    const action = drafts[runId]
    if (!action) return
    setSubmittingId(runId)
    try {
      await compareShadowRun(runId, { action })
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[runId]
        return next
      })
      await refresh()
    } finally {
      setSubmittingId(null)
    }
  }

  const renderAction = (action: string | null) => {
    if (!action) return '—'
    const label = autoActionLabel(action)
    return <Text strong>{label && label.startsWith('shadow.') ? t(label) : label}</Text>
  }

  const columns: ColumnsType<ShadowRun> = [
    { title: t('shadow.col.id'), dataIndex: 'id', key: 'id', className: 'font-mono', width: 96 },
    {
      title: t('shadow.col.graph'),
      dataIndex: 'graph_id',
      key: 'graph_id',
      className: 'font-mono',
      width: 110,
    },
    {
      title: t('shadow.card.autoAction'),
      key: 'auto',
      width: 110,
      render: (_, r) => renderAction(r.auto_action),
    },
    {
      title: t('shadow.card.verdict'),
      key: 'verdict',
      width: 104,
      render: (_, r) => {
        const verdict = comparisonVerdict(r.comparison.match)
        return <Tag color={VERDICT_COLORS[verdict]}>{t(`shadow.verdict.${verdict}`)}</Tag>
      },
    },
    {
      title: t('shadow.card.human'),
      key: 'human',
      render: (_, r) => {
        if (r.human_outcome) {
          return (
            <Space direction="vertical" size={0}>
              {renderAction(r.human_outcome.action)}
              {r.human_outcome.note && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {r.human_outcome.note}
                </Text>
              )}
            </Space>
          )
        }
        if (!canOperate) return <Text type="secondary">{t('shadow.card.noHuman')}</Text>
        return (
          <Space>
            <Select
              size="small"
              style={{ width: 150 }}
              allowClear
              placeholder={t('shadow.card.selectHuman')}
              value={drafts[r.id]}
              onChange={(v) => setDrafts((prev) => ({ ...prev, [r.id]: v }))}
              options={HUMAN_ACTION_VALUES.map((v) => ({
                value: v,
                label: t(`shadow.action.${v === 'refunded' ? 'refunded' : 'humanReview'}`),
              }))}
            />
            <Button
              size="small"
              type="link"
              loading={submittingId === r.id}
              disabled={!drafts[r.id]}
              onClick={() => submitOutcome(r.id)}
            >
              {t('shadow.card.attach')}
            </Button>
          </Space>
        )
      },
    },
    {
      title: t('shadow.col.createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 110,
      render: (v: string) => formatTime(v),
    },
  ]

  return (
    <Card
      title={t('shadow.card.title')}
      extra={
        <Button size="small" onClick={refresh} loading={loading}>
          {t('header.refresh')}
        </Button>
      }
    >
      {error && <Text type="danger">{error}</Text>}
      {!error && runs.length === 0 && !loading ? (
        <Empty description={t('shadow.card.empty')} />
      ) : (
        <Table
          size="small"
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={runs}
          pagination={false}
          scroll={{ x: 720 }}
        />
      )}
    </Card>
  )
}
