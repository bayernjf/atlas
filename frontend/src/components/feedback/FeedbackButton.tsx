import { useState } from 'react'
import { Alert, Button, Input, Modal, Radio, Typography } from 'antd'
import { submitFeedback, type FeedbackType } from '../../lib/apiClient'
import { useTranslation } from '../../locales'

const { TextArea } = Input

export function FeedbackButton() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [type, setType] = useState<FeedbackType>('bug')
  const [content, setContent] = useState('')
  const [contact, setContact] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  function close() {
    setOpen(false)
    window.setTimeout(() => {
      setType('bug')
      setContent('')
      setContact('')
      setError(null)
      setDone(false)
    }, 200)
  }

  async function submit() {
    if (!content.trim()) {
      setError(t('feedback.contentRequired'))
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await submitFeedback({ type, content: content.trim(), contact: contact.trim() })
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>{t('feedback.button')}</Button>
      <Modal
        title={t('feedback.modalTitle')}
        open={open}
        onCancel={close}
        onOk={done ? close : submit}
        confirmLoading={submitting}
        okText={done ? t('button.close') : t('feedback.submit')}
        cancelText={done ? undefined : t('button.cancel')}
        cancelButtonProps={{ style: done ? { display: 'none' } : undefined }}
      >
        {done ? (
          <Alert
            type="success"
            showIcon
            message={t('feedback.doneTitle')}
            description={t('feedback.doneDescription')}
          />
        ) : (
          <>
            <Typography.Paragraph>{t('feedback.intro')}</Typography.Paragraph>
            <Radio.Group
              value={type}
              onChange={(event) => setType(event.target.value as FeedbackType)}
              options={[
                { value: 'bug', label: t('feedback.typeBug') },
                { value: 'suggestion', label: t('feedback.typeSuggestion') },
              ]}
              optionType="button"
              buttonStyle="solid"
              style={{ marginBottom: 12 }}
            />
            <TextArea
              rows={4}
              placeholder={t('feedback.contentPlaceholder')}
              value={content}
              onChange={(event) => setContent(event.target.value)}
              maxLength={2000}
              showCount
            />
            <Input
              style={{ marginTop: 12 }}
              placeholder={t('feedback.contactPlaceholder')}
              value={contact}
              onChange={(event) => setContact(event.target.value)}
              maxLength={200}
            />
            {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
          </>
        )}
      </Modal>
    </>
  )
}
