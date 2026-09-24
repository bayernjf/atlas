import { useEffect, useState } from 'react'
import { Alert, Button, Input, InputNumber, Modal, Space, Tag, Tooltip } from 'antd'
import { useTranslation } from '../../locales'
import { getOnCall, rotateOnCall, updateOnCall, type OnCallSchedule } from '../../lib/apiClient'
import { parseMembers, validateMembers } from '../../lib/onCall'

function nextRotationDate(lastRotated: string | null | undefined, intervalDays: number): string {
  const last = new Date(`${(lastRotated ?? '').slice(0, 10)}T00:00:00Z`)
  const today = new Date()
  today.setUTCHours(0, 0, 0, 0)
  let next = Number.isNaN(last.getTime()) ? new Date(today.getTime()) : new Date(last)
  let guard = 0
  while (next.getTime() < today.getTime() && guard < 400) {
    next = new Date(next.getTime() + intervalDays * 86_400_000)
    guard += 1
  }
  return next.toISOString().slice(0, 10)
}

/** docs/33 §5.4：告警 Card 顶部值班条（当前值班人 + 轮换 + 设置轮值表，写操作仅 admin）。 */
export function OnCallBar({ canAdmin, onChanged }: { canAdmin: boolean; onChanged?: () => void }) {
  const { t } = useTranslation('monitoring')
  const [schedule, setSchedule] = useState<OnCallSchedule | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [memberText, setMemberText] = useState('')
  const [modalError, setModalError] = useState('')
  const [saving, setSaving] = useState(false)
  const [intervalDays, setIntervalDays] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    getOnCall()
      .then((data) => {
        if (!cancelled) setSchedule(data)
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

  const openModal = () => {
    setMemberText((schedule?.members ?? []).join(', '))
    setIntervalDays(schedule?.rotation_interval_days ?? null)
    setModalError('')
    setModalOpen(true)
  }

  const handleRotate = async () => {
    try {
      setError('')
      setSchedule(await rotateOnCall())
      onChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const handleSave = async () => {
    const members = parseMembers(memberText)
    const errorKey = validateMembers(members)
    if (errorKey) {
      setModalError(t(errorKey))
      return
    }
    setSaving(true)
    try {
      setSchedule(await updateOnCall(members, intervalDays))
      setModalOpen(false)
      onChanged?.()
    } catch (err) {
      setModalError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const current = schedule?.current ?? null
  const total = schedule?.members.length ?? 0

  return (
    <div style={{ marginBottom: 12 }}>
      <Space wrap>
        <span>{t('onCall.label')}</span>
        {loading ? (
          <Tag>{t('onCall.loading')}</Tag>
        ) : current ? (
          <Tooltip title={t('onCall.rotationHint', { index: (schedule?.index ?? 0) + 1, total })}>
            <Tag color="blue">{current}</Tag>
          </Tooltip>
        ) : (
          <Tag>{t('onCall.nobody')}</Tag>
        )}
        {schedule?.rotation_interval_days ? (
          <Tag color="geekblue">
            {t('onCall.autoTag', {
              days: schedule.rotation_interval_days,
              date: nextRotationDate(schedule.last_rotated_at, schedule.rotation_interval_days),
            })}
          </Tag>
        ) : null}
        {canAdmin && (
          <>
            <Button size="small" disabled={total === 0} onClick={handleRotate}>
              {t('onCall.rotate')}
            </Button>
            <Button size="small" onClick={openModal}>
              {t('onCall.configure')}
            </Button>
          </>
        )}
        {error && <Alert type="error" showIcon message={error} style={{ padding: '2px 8px' }} />}
      </Space>

      <Modal
        title={t('onCall.modalTitle')}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        okText={t('onCall.save')}
        cancelText={t('common:button.cancel')}
        afterClose={() => {
          setMemberText('')
          setModalError('')
        }}
      >
        <p style={{ color: '#888' }}>{t('onCall.modalHint')}</p>
        <Input.TextArea
          rows={3}
          value={memberText}
          onChange={(event) => setMemberText(event.target.value)}
          placeholder={t('onCall.placeholder')}
        />
        <div style={{ marginTop: 12 }}>
          <div style={{ marginBottom: 4 }}>{t('onCall.intervalLabel')}</div>
          <InputNumber
            min={1}
            max={365}
            value={intervalDays}
            placeholder={t('onCall.intervalLabel')}
            onChange={(value) => setIntervalDays(typeof value === 'number' ? value : null)}
          />
        </div>
        {modalError && <Alert type="error" showIcon message={modalError} style={{ marginTop: 8 }} />}
      </Modal>
    </div>
  )
}
