import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Collapse,
  Input,
  InputNumber,
  Popconfirm,
  Popover,
  Select,
  Space,
  Table,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useTranslation } from '../../locales'
import {
  createSilence,
  deleteSilence,
  listSilences,
  type Silence,
} from '../../lib/apiClient'
import { formatTime } from '../../lib/monitoring'

const DURATION_PRESETS = [30, 60, 240, 1440]
const CUSTOM = -1
const MIN_MINUTES = 1
const MAX_MINUTES = 10080

/** docs/33 §5.4：告警操作列「静默」按钮（带规则/图上下文、时长、原因；仅 admin 渲染）。 */
export function SilencePopButton({
  ruleId,
  graphId,
  onCreated,
}: {
  ruleId: string
  graphId: string
  onCreated?: () => void
}) {
  const { t } = useTranslation('monitoring')
  const [open, setOpen] = useState(false)
  const [duration, setDuration] = useState<number>(60)
  const [customMinutes, setCustomMinutes] = useState<number>(60)
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const reset = () => {
    setDuration(60)
    setCustomMinutes(60)
    setReason('')
    setError('')
  }

  const handleSubmit = async () => {
    const minutes = duration === CUSTOM ? customMinutes : duration
    const trimmed = reason.trim()
    if (!trimmed) {
      setError(t('silence.error.reasonRequired'))
      return
    }
    if (!Number.isInteger(minutes) || minutes < MIN_MINUTES || minutes > MAX_MINUTES) {
      setError(t('silence.error.durationRange'))
      return
    }
    setSubmitting(true)
    try {
      await createSilence({
        rule_id: ruleId || null,
        graph_id: graphId || null,
        duration_minutes: minutes,
        reason: trimmed,
      })
      setOpen(false)
      reset()
      onCreated?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  const content = (
    <Space orientation="vertical" size={8} style={{ width: 260 }}>
      <div>
        <div style={{ marginBottom: 4 }}>{t('silence.durationLabel')}</div>
        <Select
          value={duration}
          style={{ width: '100%' }}
          onChange={setDuration}
          options={[
            ...DURATION_PRESETS.map((value) => ({
              value,
              label: t('silence.durationMinutes', { minutes: value }),
            })),
            { value: CUSTOM, label: t('silence.durationCustom') },
          ]}
        />
      </div>
      {duration === CUSTOM && (
        <InputNumber
          min={MIN_MINUTES}
          max={MAX_MINUTES}
          value={customMinutes}
          style={{ width: '100%' }}
          onChange={(value) => setCustomMinutes(typeof value === 'number' ? value : 60)}
        />
      )}
      <div>
        <div style={{ marginBottom: 4 }}>{t('silence.reasonLabel')}</div>
        <Input.TextArea
          rows={2}
          maxLength={200}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder={t('silence.reasonPlaceholder')}
        />
      </div>
      {error && <Alert type="error" showIcon message={error} />}
      <Space>
        <Button type="primary" size="small" loading={submitting} onClick={handleSubmit}>
          {t('silence.confirm')}
        </Button>
        <Button size="small" onClick={() => setOpen(false)}>
          {t('common:button.cancel')}
        </Button>
      </Space>
    </Space>
  )

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
      trigger="click"
      title={t('silence.popTitle')}
      content={content}
    >
      <Button size="small">{t('silence.action')}</Button>
    </Popover>
  )
}

/** docs/33 §5.4：折叠的静默规则列表（规则/图/原因/创建人/到期/压下次数/状态/删除）。 */
export function SilenceManager({ canAdmin }: { canAdmin: boolean }) {
  const { t } = useTranslation('monitoring')
  const [silences, setSilences] = useState<Silence[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const reload = useCallback(() => {
    let cancelled = false
    listSilences()
      .then((data) => {
        if (!cancelled) setSilences(data)
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

  useEffect(() => reload(), [reload])

  const handleDelete = async (id: string) => {
    try {
      await deleteSilence(id)
      setSilences((current) => current.filter((item) => item.id !== id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const columns: ColumnsType<Silence> = [
    {
      title: t('silence.col.rule'),
      dataIndex: 'rule_id',
      width: 130,
      render: (ruleId: string | null) => ruleId ?? t('silence.allRules'),
    },
    {
      title: t('silence.col.graph'),
      dataIndex: 'graph_id',
      width: 120,
      render: (graphId: string | null) => graphId ?? t('silence.allGraphs'),
    },
    { title: t('silence.col.reason'), dataIndex: 'reason' },
    { title: t('silence.col.createdBy'), dataIndex: 'created_by', width: 100 },
    {
      title: t('silence.col.expiresAt'),
      dataIndex: 'expires_at',
      width: 150,
      render: (value: string) => formatTime(value),
    },
    { title: t('silence.col.suppressed'), dataIndex: 'suppressed_count', width: 90 },
    {
      title: t('silence.col.status'),
      dataIndex: 'active',
      width: 90,
      render: (active: boolean | undefined) =>
        active ? <Tag color="green">{t('silence.active')}</Tag> : <Tag>{t('silence.expired')}</Tag>,
    },
    ...(canAdmin
      ? [
          {
            title: t('silence.col.actions'),
            key: 'actions',
            width: 80,
            render: (_: unknown, record: Silence) => (
              <Popconfirm
                title={t('silence.deleteConfirm')}
                okText={t('common:button.confirm')}
                cancelText={t('common:button.cancel')}
                onConfirm={() => handleDelete(record.id)}
              >
                <Button size="small" danger>
                  {t('silence.delete')}
                </Button>
              </Popconfirm>
            ),
          } as ColumnsType<Silence>[number],
        ]
      : []),
  ]

  const activeCount = silences.filter((item) => item.active).length

  return (
    <Collapse
      ghost
      style={{ marginTop: 12 }}
      items={[
        {
          key: 'silences',
          label: t('silence.managerTitle', { active: activeCount, total: silences.length }),
          children: (
            <>
              {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 8 }} />}
              <Table
                rowKey="id"
                size="small"
                loading={loading}
                columns={columns}
                dataSource={silences}
                pagination={false}
              />
            </>
          ),
        },
      ]}
    />
  )
}
