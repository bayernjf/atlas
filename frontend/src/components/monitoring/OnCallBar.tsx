import { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Space, Tag, Tooltip } from 'antd'
import { useTranslation } from '../../locales'
import { getOnCall, rotateOnCall, updateOnCall, type OnCallSchedule } from '../../lib/apiClient'
import { parseMembers, validateMembers } from '../../lib/onCall'

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
      setSchedule(await updateOnCall(members))
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
        {modalError && <Alert type="error" showIcon message={modalError} style={{ marginTop: 8 }} />}
      </Modal>
    </div>
  )
}
